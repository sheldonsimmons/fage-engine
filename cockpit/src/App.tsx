import { Scorecard } from "@/components/Scorecard"

function App() {
  return (
    <div className="min-h-screen bg-background">
      <div className="mx-auto max-w-6xl px-6 py-10">
        <header className="mb-8">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">CostPilot</p>
          <h1 className="text-2xl font-semibold tracking-tight">Executive Scorecard</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Real-time spend, savings, budget, and setup health for this workspace.
          </p>
        </header>
        <Scorecard />
      </div>
    </div>
  )
}

export default App
