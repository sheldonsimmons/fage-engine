import { useState } from "react"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Button } from "@/components/ui/button"
import { askCostPilot } from "@/lib/api"
import { Send, Sparkles } from "lucide-react"

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
          <div className="mt-4 whitespace-pre-wrap rounded-md border bg-muted/30 p-4 text-sm">{answer}</div>
        )}
      </CardContent>
    </Card>
  )
}
