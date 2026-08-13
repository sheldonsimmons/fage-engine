import {
  LayoutDashboard,
  DollarSign,
  Bot,
  Target,
  Zap,
  Wallet,
  FileBarChart,
  Sparkles,
  Building2,
  Plug,
  Settings,
} from "lucide-react"

// Only two items link anywhere real: Business Profiles and Integrations,
// because those pages already exist (business-profile.html,
// connector-manager.html) in the existing vanilla-JS site. Everything else
// in the mockup's nav (AI Spend, Agents, Business Impact, Budgets,
// Reports...) doesn't have its own page yet -- rendered visibly but inert
// rather than as dead links or fabricated routes.
const primaryNav = [
  { label: "Executive Overview", icon: LayoutDashboard, active: true },
  { label: "AI Spend", icon: DollarSign },
  { label: "Agents", icon: Bot },
  { label: "Business Impact", icon: Target },
  { label: "Savings & Optimization", icon: Zap },
  { label: "Budgets", icon: Wallet },
  { label: "Reports", icon: FileBarChart },
  { label: "Ask CostPilot", icon: Sparkles },
]

const contextNav = [
  { label: "Business Profiles", icon: Building2, href: "/business-profile.html" },
  { label: "Integrations", icon: Plug, href: "/connector-manager.html" },
]

export function AppSidebar() {
  return (
    <aside className="hidden w-64 shrink-0 border-r border-border bg-card md:flex md:flex-col">
      <div className="flex items-center gap-2 px-5 py-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-primary text-primary-foreground">
          <LayoutDashboard className="h-4 w-4" />
        </div>
        <span className="text-sm font-semibold tracking-wide">
          COST<span className="text-emerald-400">PILOT</span>
        </span>
      </div>

      <nav className="flex-1 space-y-1 px-3">
        {primaryNav.map((item) => (
          <div
            key={item.label}
            className={`flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
              item.active
                ? "bg-primary/10 font-medium text-foreground"
                : "text-muted-foreground/70"
            }`}
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </div>
        ))}

        <p className="px-3 pt-5 pb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/60">
          Business Context
        </p>
        {contextNav.map((item) => (
          <a
            key={item.label}
            href={item.href}
            className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </a>
        ))}
      </nav>

      <div className="border-t border-border px-3 py-4">
        <div className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground/70">
          <Settings className="h-4 w-4" />
          Settings
        </div>
      </div>
    </aside>
  )
}
