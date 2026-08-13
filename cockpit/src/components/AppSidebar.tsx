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

// Real pages in the existing vanilla-JS site, matched honestly to each
// label rather than left inert -- confirmed by reading each page's actual
// <title> before wiring the link, not guessed from the filename.
const primaryNav = [
  { label: "Executive Overview", icon: LayoutDashboard, active: true },
  { label: "AI Spend", icon: DollarSign, href: "/operate.html" },
  { label: "Agents", icon: Bot, href: "/connect.html" },
  { label: "Business Impact", icon: Target, href: "/work-items.html" },
  { label: "Savings & Optimization", icon: Zap, href: "/savings.html" },
  { label: "Budgets", icon: Wallet, href: "/admin.html" },
  { label: "Reports", icon: FileBarChart, href: "/reports.html" },
  // No href -- the Ask CostPilot panel is already on this same page,
  // further down, so this scrolls to it instead of navigating away.
  { label: "Ask CostPilot", icon: Sparkles, anchor: "#ask-costpilot" },
]

const contextNav = [
  { label: "Business Profiles", icon: Building2, href: "/business-profile.html" },
  { label: "Integrations", icon: Plug, href: "/connector-manager.html" },
]

function NavItem({ item }: { item: (typeof primaryNav)[number] }) {
  const className = `flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
    item.active
      ? "bg-primary/10 font-medium text-foreground"
      : "text-muted-foreground hover:bg-accent hover:text-foreground"
  }`
  if (item.active) {
    return (
      <div className={className}>
        <item.icon className="h-4 w-4" />
        {item.label}
      </div>
    )
  }
  return (
    <a href={item.href ?? item.anchor} className={className}>
      <item.icon className="h-4 w-4" />
      {item.label}
    </a>
  )
}

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
          <NavItem key={item.label} item={item} />
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
        <a
          href="/admin.html"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <Settings className="h-4 w-4" />
          Settings
        </a>
      </div>
    </aside>
  )
}
