import { useState } from "react"
import ReactMarkdown from "react-markdown"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { askCostPilot } from "@/lib/api"
import { Send, Sparkles } from "lucide-react"

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

const SUGGESTIONS = ["What is our total spend this month?", "Which department is over budget?", "What can we optimize?"]

export function AskCostPilot({ workspaceId }: { workspaceId: string }) {
  const [question, setQuestion] = useState("")
  const [answer, setAnswer] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function ask(q: string) {
    if (!q.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await askCostPilot(workspaceId, q)
      setAnswer(res.answer)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.")
    } finally {
      setLoading(false)
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
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="What would you like to know about your AI usage?"
          />
          <Button type="submit" size="icon" disabled={loading}>
            <Send className="h-4 w-4" />
          </Button>
        </form>
        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => {
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
        {answer && !loading && (
          <div className="mt-4 rounded-md border bg-muted/30 p-4 text-sm">
            <ReactMarkdown components={markdownComponents}>{answer}</ReactMarkdown>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
