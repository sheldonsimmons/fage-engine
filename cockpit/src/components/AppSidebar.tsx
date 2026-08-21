import {
  Home,
  LayoutDashboard,
  Activity,
  Sparkles,
  Building2,
  FileBarChart,
  Plug,
  LayoutGrid,
  Settings,
} from "lucide-react"

// This list is kept in exact sync with left-nav.js (the single real
// navigation used by every other page in the app) -- same items, same
// order, same hrefs -- so the product has one standard nav instead of
// this page drifting its own. "Executive Dashboard" is the one entry
// that's the current page here, so it renders as the active item with
// no href instead of linking to /cockpit/ (which is where it would point
// on every other page's copy of this list).
const primaryNav = [
  { label: "Home", icon: Home, href: "/index.html" },
  { label: "Executive Dashboard", icon: LayoutDashboard, active: true },
  { label: "AI Activity", icon: Activity, href: "/operate.html" },
  // No href -- the Ask CostPilot panel is already on this same page,
  // further down, so this scrolls to it instead of navigating away.
  { label: "Ask CostPilot", icon: Sparkles, anchor: "#ask-costpilot" },
  { label: "Business Profiles", icon: Building2, href: "/business-profile.html" },
  { label: "Reports", icon: FileBarChart, href: "/reports.html" },
  { label: "Integrations", icon: Plug, href: "/onboarding.html" },
  { label: "Connectors", icon: LayoutGrid, href: "/connector-manager.html" },
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
