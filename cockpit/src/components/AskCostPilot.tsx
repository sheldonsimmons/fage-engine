import { useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { AskProposalCard } from "@/components/AskProposalCard"
import { askCostPilot, speakText, transcribeVoiceQuestion, type AskAnswer } from "@/lib/api"
import { Mic, Send, Sparkles, Volume2 } from "lucide-react"

// The backend returns markdown (bold, bullet/numbered lists) meant to be
// rendered, not shown as raw "**text**" -- these overrides just apply the
// dashboard's existing type scale/spacing to each markdown element instead
// of pulling in a full prose plugin for one panel.
const markdownComponents = {
  p: (props: React.ComponentProps<"p">) => <p className="mb-2 last:mb-0" {...props} />,
  strong: (props: React.ComponentProps<"strong">) => <strong className="font-semibold text-foreground" {...props} />,
  ul: (props: React.ComponentProps<"ul">) => <ul className="mb-2 list-disc space-y-1 pl-5 last:mb-0" {...props} />,
  ol: (props: React.ComponentProps<"ol">) => <ol className="mb-2 list-decimal space-y-1 pl-5 last:mb-0" {...props} />,
  li: (props: React.ComponentProps<"li">) => <li {...props} />,
  h1: (props: React.ComponentProps<"h1">) => <h1 className="mb-2 text-base font-semibold" {...props} />,
  h2: (props: React.ComponentProps<"h2">) => <h2 className="mb-2 text-sm font-semibold" {...props} />,
  h3: (props: React.ComponentProps<"h3">) => <h3 className="mb-1 text-sm font-semibold" {...props} />,
  code: (props: React.ComponentProps<"code">) => <code className="rounded bg-muted px-1 py-0.5 text-xs" {...props} />,
}

// data_provenance.scope's real values (data_provenance in the API
// response, see AskAnswer in lib/api.ts) -- a separate vocabulary from
// the Measured/Estimated/Associated/Insufficient-Data trust
// classification (that one lives on individual metrics elsewhere, e.g.
// KpiRow.tsx's EvidenceBadge; Ask CostPilot's own answer payload doesn't
// carry a per-evidence trust label today, so this badge is intentionally
// the only trust/scope signal shown here -- no fabricated ones added).
const PROVENANCE_LABEL: Record<string, string> = {
  live: "Live Data",
  simulator: "Simulator",
  mixed: "Mixed",
  no_activity: "Insufficient Data",
}

function ProvenanceBadge({ scope }: { scope: string }) {
  const label = PROVENANCE_LABEL[scope] ?? scope
  return (
    <span className="rounded-full border px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide">
      {label}
    </span>
  )
}

// Every suggestion here is verified against the real deterministic
// parser (_ask_intent() in api/routes_efficiency.py) to resolve to a
// specific, useful intent -- not a generic overview fallback. Three of
// the original five didn't ("Where are we wasting money?", "Are our AI
// economics improving?", "Which agents need attention?" all fell back
// to intent="overview" despite reading like real, answerable
// questions) -- the worst place for that gap to live, since these are
// the exact questions meant to show a new user what the product can
// actually do. Re-verify with _ask_intent() before changing any of
// these again.
const SUGGESTIONS = [
  "Why did AI spend increase?",
  "How can we save money on AI?",
  "Is our AI spend trending up or down?",
  "Which agents are inactive or underused?",
  "What should I review first?",
]

// pendingQuestion lets other sections of the page (e.g. the
// Recommendation hero card) hand this component a question to ask on
// the user's behalf -- incrementing `nonce` each time re-triggers the
// effect below even if the same question text is asked twice in a row.
// This never calls a new/different backend endpoint: it's the exact
// same askCostPilot() call the suggestion chips already make.
export function AskCostPilot({
  workspaceId,
  pendingQuestion,
}: {
  workspaceId: string
  pendingQuestion?: { text: string; nonce: number }
}) {
  const [question, setQuestion] = useState("")
  const [data, setData] = useState<AskAnswer | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // CostPilot Voice (Phase 1) -- mirrors global-nav.js's push-to-talk
  // flow (backend/api/routes_ask_voice.py), minus the opt-in "Hey
  // CostPilot" wake-word listener (off by default there too, bigger
  // scope than basic voice access). pendingVoiceMeta tags the NEXT ask()
  // call as voice-originated, then is cleared -- same "only stays voice
  // for the send that follows recording" rule the legacy version uses.
  const [recording, setRecording] = useState(false)
  const [voiceStatus, setVoiceStatus] = useState<string | null>(null)
  const [speaking, setSpeaking] = useState(false)
  const pendingVoiceMeta = useRef<{ confidence: number | null } | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const currentAudioRef = useRef<HTMLAudioElement | null>(null)

  useEffect(() => {
    if (!pendingQuestion) return
    setQuestion(pendingQuestion.text)
    ask(pendingQuestion.text)
    // Same id + getElementById pattern App.tsx's own #ask-costpilot
    // hash-scroll effect already uses -- a ref on the shadcn Card here
    // was unreliable (confirmed live: the hero button's scroll silently
    // did nothing), this is the proven path.
    document.getElementById("ask-costpilot")?.scrollIntoView({ behavior: "auto", block: "start" })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingQuestion?.nonce])

  async function ask(q: string, pinnedFilter?: { name: string; value: string | number } | null) {
    if (!q.trim()) return
    const voiceMeta = pendingVoiceMeta.current
    pendingVoiceMeta.current = null
    // Keeps the status line continuous across "review your question" ->
    // "thinking" instead of it going blank the instant loading starts --
    // still the same real, unavoidable answer-generation wait as the
    // typed path, just narrated for a voice-originated question too.
    if (voiceMeta) setVoiceStatus("Thinking…")
    setLoading(true)
    setError(null)
    try {
      const res = await askCostPilot(workspaceId, q, voiceMeta, pinnedFilter)
      setData(res)
      // Voice in, voice back out -- a typed question never auto-plays,
      // matching global-nav.js's speakAskAnswer() call site. The answer
      // is already fully generated by this point -- synthesizing speech
      // for it is a separate, real backend call that can't start any
      // earlier, so this is genuinely more sequential work than the
      // typed path, not a stall. Status text says so instead of leaving
      // silence between "answer arrived" and "audio starts."
      if (voiceMeta && res.answer) {
        setVoiceStatus("Generating voice reply…")
        speak(res.answer)
      } else if (voiceMeta) {
        setVoiceStatus(null)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.")
      if (voiceMeta) setVoiceStatus(null)
    } finally {
      setLoading(false)
    }
  }

  function stopSpeaking() {
    currentAudioRef.current?.pause()
    currentAudioRef.current = null
    setSpeaking(false)
  }

  async function speak(text: string) {
    stopSpeaking()
    try {
      const blob = await speakText(text)
      const audio = new Audio(URL.createObjectURL(blob))
      currentAudioRef.current = audio
      audio.onended = () => setSpeaking(false)
      setVoiceStatus(null)
      setSpeaking(true)
      await audio.play()
    } catch {
      setVoiceStatus(null)
      setSpeaking(false)
    }
  }

  async function toggleRecording() {
    if (recording) {
      mediaRecorderRef.current?.stop()
      return
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setVoiceStatus("Voice isn't supported in this browser — try typing instead.")
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      audioChunksRef.current = []
      const recorder = new MediaRecorder(stream)
      recorder.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data) }
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop())
        setRecording(false)
        transcribe(recorder.mimeType)
      }
      mediaRecorderRef.current = recorder
      recorder.start()
      setRecording(true)
      setVoiceStatus("Listening… click the mic again to stop.")
    } catch {
      setVoiceStatus("Couldn't access your microphone — check your browser permissions.")
    }
  }

  async function transcribe(mimeType: string) {
    if (!audioChunksRef.current.length) { setVoiceStatus(null); return }
    setVoiceStatus("Transcribing…")
    const blob = new Blob(audioChunksRef.current, { type: mimeType || "audio/webm" })
    try {
      const { transcript, confidence } = await transcribeVoiceQuestion(blob)
      setQuestion(transcript)
      pendingVoiceMeta.current = { confidence }
      const lowConfidence = confidence !== null && confidence < 0.5
      setVoiceStatus(
        lowConfidence
          ? "Not fully sure I caught that — check the text below before sending."
          : "Review your question, then hit Ask.",
      )
    } catch (err) {
      pendingVoiceMeta.current = null
      setVoiceStatus(err instanceof Error ? err.message : "Could not transcribe that clip. Try typing instead.")
    }
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="mb-3 flex items-center gap-2 text-sm font-medium">
          <Sparkles className="h-4 w-4" />
          Ask CostPilot
        </div>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            ask(question)
          }}
        >
          <Input
            value={question}
            onChange={(e) => { setQuestion(e.target.value); pendingVoiceMeta.current = null }}
            placeholder="What would you like to know about your AI usage?"
          />
          <Button
            type="button"
            size="icon"
            variant={recording ? "default" : "outline"}
            onClick={toggleRecording}
            aria-label="Ask by voice"
            title="Ask by voice"
          >
            <Mic className={`h-4 w-4 ${recording ? "animate-pulse" : ""}`} />
          </Button>
          <Button type="submit" size="icon" disabled={loading}>
            <Send className="h-4 w-4" />
          </Button>
        </form>
        {voiceStatus && <p className="mt-2 text-xs text-muted-foreground">{voiceStatus}</p>}
        {speaking && (
          <button
            type="button"
            onClick={stopSpeaking}
            className="mt-2 inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs text-muted-foreground hover:bg-accent"
          >
            <Volume2 className="h-3 w-3 animate-pulse" />
            Speaking… click to stop
          </button>
        )}
        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => {
                pendingVoiceMeta.current = null
                setQuestion(s)
                ask(s)
              }}
              className="rounded-full border px-3 py-1 text-xs text-muted-foreground hover:bg-accent"
            >
              {s}
            </button>
          ))}
        </div>
        {loading && <p className="mt-4 text-sm text-muted-foreground">Thinking…</p>}
        {error && <p className="mt-4 text-sm text-destructive">{error}</p>}
        {data && !loading && (
          <div className="mt-4 rounded-md border bg-muted/30 p-4 text-sm">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {data.workspace_name && <span>{data.workspace_name}</span>}
              {data.data_provenance?.scope && <ProvenanceBadge scope={data.data_provenance.scope} />}
            </div>

            {data.budget_flag && data.budget_flag.severity !== "ok" && data.budget_flag.severity !== "unknown" && (
              <div className={`mb-2 space-y-1 rounded-md px-3 py-1.5 text-xs ${data.budget_flag.severity === "critical" ? "bg-red-500/10 text-red-700" : "bg-amber-500/10 text-amber-700"}`}>
                {[...data.budget_flag.over_budget, ...data.budget_flag.near_cap].map((flag, i) => (
                  <p key={i}>{flag.detail || `${flag.department} is at budget risk.`}</p>
                ))}
              </div>
            )}

            <ReactMarkdown components={markdownComponents}>{data.answer}</ReactMarkdown>

            {data.proposal && <AskProposalCard proposal={data.proposal} />}

            {!!data.evidence?.length && (
              <div className="mt-3 space-y-1.5">
                {data.evidence.map((item, i) => {
                  const isClarification = data.intent === "clarification_required"
                  const pinnable = item.filter_name && item.filter_value !== null && item.filter_value !== undefined
                    ? { name: item.filter_name, value: item.filter_value }
                    : null
                  const followUpQuestion = isClarification
                    ? item.question || item.label || ""
                    : `Tell me more about ${item.label || "this"}.`
                  return (
                    <button
                      key={`${item.label}-${i}`}
                      type="button"
                      onClick={() => {
                        pendingVoiceMeta.current = null
                        setQuestion(followUpQuestion)
                        ask(followUpQuestion, pinnable)
                      }}
                      className="flex w-full items-center justify-between gap-3 rounded-md border bg-background px-3 py-2 text-left text-xs hover:bg-accent"
                    >
                      <span>
                        <span className="font-medium">{item.label || "Unknown"}</span>
                        {item.detail && <span className="text-muted-foreground"> — {item.detail}</span>}
                      </span>
                      <span className="shrink-0 tabular-nums text-muted-foreground">
                        {item.value || "—"}{item.metric_label ? ` ${item.metric_label}` : ""}
                      </span>
                    </button>
                  )
                })}
              </div>
            )}

            {data.calculation?.formula && (
              <p className="mt-3 text-xs text-muted-foreground">
                <span className="font-medium">How this was calculated: </span>
                {data.calculation.formula}
                {typeof data.calculation.row_count === "number" && ` (${data.calculation.row_count.toLocaleString()} rows)`}
              </p>
            )}

            {!!data.recommendations?.length && (
              <div className="mt-3 space-y-1">
                <p className="text-xs font-medium">Recommended next steps</p>
                {data.recommendations.map((rec, i) => (
                  <p key={i} className="text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">{rec.title}</span>
                    {rec.body ? ` — ${rec.body}` : ""}
                  </p>
                ))}
              </div>
            )}

            {!!data.suggested_questions?.length && (
              <div className="mt-3 flex flex-wrap gap-2">
                {data.suggested_questions.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => { pendingVoiceMeta.current = null; setQuestion(s); ask(s) }}
                    className="rounded-full border px-2.5 py-1 text-xs text-muted-foreground hover:bg-accent"
                  >
                    {s}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}
