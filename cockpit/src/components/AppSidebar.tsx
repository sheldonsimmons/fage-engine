import { useEffect, useState } from "react"
import {
  Home,
  LayoutDashboard,
  Activity,
  Sparkles,
  Building2,
  FileBarChart,
  Plug,
  LayoutGrid,
  Layers,
  Target,
  Settings,
  type LucideIcon,
} from "lucide-react"

interface NavItemData {
  label: string
  href: string
  icon: string
}

// Icon key -> component, matching left-nav.js's own icon key names (see
// frontend/js/left-nav.js's `icons` map) so both renderers can consume
// the same /nav-items.json entries.
const ICONS: Record<string, LucideIcon> = {
  home: Home,
  dashboard: LayoutDashboard,
  pulse: Activity,
  ask: Sparkles,
  building: Building2,
  doc: FileBarChart,
  plug: Plug,
  grid: LayoutGrid,
  layers: Layers,
  target: Target,
  gear: Settings,
}

// Falls back to this if the fetch fails, same defensive pattern
// left-nav.js uses for its own FALLBACK_ITEMS -- keeps the sidebar
// rendering something reasonable rather than nothing.
const FALLBACK_ITEMS: NavItemData[] = [
  { label: "Home", href: "/index.html", icon: "home" },
  { label: "Executive Dashboard", href: "/cockpit/", icon: "dashboard" },
  { label: "AI Activity", href: "/operate.html", icon: "pulse" },
  { label: "Ask CostPilot", href: "#", icon: "ask" },
  { label: "Business Profiles", href: "/business-profile.html", icon: "building" },
  { label: "Reports", href: "/reports.html", icon: "doc" },
  { label: "Integrations", href: "/onboarding.html", icon: "plug" },
  { label: "Connectors", href: "/connector-manager.html", icon: "grid" },
  { label: "Models", href: "/models.html", icon: "layers" },
  { label: "Policy", href: "/policy.html", icon: "target" },
  { label: "Settings", href: "/admin.html", icon: "gear" },
]

// This label's href in the shared data is a placeholder ("#") -- on this
// page specifically, Ask CostPilot is a panel further down the same
// page, so it scrolls there instead of navigating away. left-nav.js
// wires its own, different Ask CostPilot behavior for its pages; each
// renderer owns this override, it isn't shared nav data.
const ASK_COSTPILOT_ANCHOR = "#ask-costpilot"

function useNavItems(): NavItemData[] {
  const [items, setItems] = useState<NavItemData[]>(FALLBACK_ITEMS)
  useEffect(() => {
    let cancelled = false
    fetch("/nav-items.json")
      .then((res) => (res.ok ? res.json() : Promise.reject(res.status)))
      .then((data) => {
        if (!cancelled && Array.isArray(data.items) && data.items.length) setItems(data.items)
      })
      .catch(() => {
        // Fetch failed -- FALLBACK_ITEMS (already the initial state) stands.
      })
    return () => { cancelled = true }
  }, [])
  return items
}

function NavItem({ item }: { item: NavItemData }) {
  const isCurrentPage = item.href === "/cockpit/"
  const href = item.label === "Ask CostPilot" ? ASK_COSTPILOT_ANCHOR : item.href
  const Icon = ICONS[item.icon] ?? Home
  const className = `flex items-center gap-3 rounded-md px-3 py-2 text-sm ${
    isCurrentPage
      ? "bg-primary/10 font-medium text-foreground"
      : "text-muted-foreground hover:bg-accent hover:text-foreground"
  }`
  if (isCurrentPage) {
    return (
      <div className={className}>
        <Icon className="h-4 w-4" />
        {item.label}
      </div>
    )
  }
  return (
    <a href={href} className={className}>
      <Icon className="h-4 w-4" />
      {item.label}
    </a>
  )
}

export function AppSidebar() {
  const items = useNavItems()
  // Settings gets its own footer treatment, same visual placement as
  // before this was data-driven -- everything else renders in order.
  const primaryItems = items.filter((item) => item.label !== "Settings")
  const settingsItem = items.find((item) => item.label === "Settings")

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
        {primaryItems.map((item) => (
          <NavItem key={item.label} item={item} />
        ))}
      </nav>

      <div className="border-t border-border px-3 py-4">
        <a
          href={settingsItem?.href ?? "/admin.html"}
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <Settings className="h-4 w-4" />
          Settings
        </a>
      </div>
    </aside>
  )
}
