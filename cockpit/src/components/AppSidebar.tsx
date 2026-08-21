import {
  Home,
  LayoutDashboard,
  DollarSign,
  Bot,
  Target,
  FileBarChart,
  Sparkles,
  Building2,
  Plug,
  Settings,
} from "lucide-react"

// Labels matched to what left-nav.js (the existing vanilla-JS site's real
// navigation) already calls these same pages, not invented mockup-style
// names -- "Home" (index.html), "AI Activity", and "Connectors" are the product's real,
// established names for operate.html/connector-manager.html. "Agents"
// (connect.html) has no real-nav precedent but is a genuinely distinct
// destination. "Budgets" was dropped -- it pointed at the exact same page
// (admin.html) as Settings under a different name, which is the same kind
// of misleading duplication as everything else corrected this session.
// "Optimization" was dropped too -- it pointed at savings.html (the
// pre-sale "3-minute savings calculator" marketing page) until that page
// was retired, and operate.html is already reachable via "AI Activity",
// so repointing it there would have been a duplicate entry.
const primaryNav = [
  { label: "Home", icon: Home, href: "/index.html" },
  { label: "Executive Overview", icon: LayoutDashboard, active: true },
  { label: "AI Activity", icon: DollarSign, href: "/operate.html" },
  { label: "Agents", icon: Bot, href: "/connect.html" },
  { label: "Business Impact", icon: Target, href: "/work-items.html" },
  { label: "Reports", icon: FileBarChart, href: "/reports.html" },
  // No href -- the Ask CostPilot panel is already on this same page,
  // further down, so this scrolls to it instead of navigating away.
  { label: "Ask CostPilot", icon: Sparkles, anchor: "#ask-costpilot" },
]

const contextNav = [
  { label: "Business Profiles", icon: Building2, href: "/business-profile.html" },
  { label: "Connectors", icon: Plug, href: "/connector-manager.html" },
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
