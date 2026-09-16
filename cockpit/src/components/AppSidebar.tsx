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
  Users,
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
  user: Users,
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
  { label: "Users", href: "/users.html", icon: "user" },
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
      ? "bg-sidebar-primary/15 font-medium text-sidebar-foreground"
      : "text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
  }`
  if (isCurrentPage) {
    return (
      <div className={className}>
        <Icon className="h-4 w-4" />
        {item.label}
      </div>
    )
  }
  // Ask CostPilot jumps straight to the widget already on this page --
  // instant, not a smooth scroll-over-time, so it reads as "go to Ask
  // CostPilot" rather than "scroll down a bit" (confirmed live: the
  // smooth-scroll version felt like it hadn't done anything).
  const onClick = item.label === "Ask CostPilot"
    ? (e: React.MouseEvent) => {
        e.preventDefault()
        document.getElementById("ask-costpilot")?.scrollIntoView({ behavior: "auto", block: "start" })
      }
    : undefined
  return (
    <a href={href} className={className} onClick={onClick}>
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
    <aside className="hidden w-64 shrink-0 border-r border-sidebar-border bg-sidebar text-sidebar-foreground md:flex md:flex-col">
      <div className="flex items-center px-5 py-5">
        {/* Same brand asset used across every legacy page header and the
            favicon (frontend/assets/costpilot-logo.png) -- one image, not
            a bespoke icon+text JSX rendering, so the mark is identical
            wherever it appears. Root-relative path resolves correctly
            from a /cockpit/-mounted page since both are served by the
            same app. */}
        <img src="/assets/costpilot-logo.png" alt="CostPilot" className="h-8 w-auto object-contain" />
      </div>

      <nav className="flex-1 space-y-1 px-3">
        {primaryItems.map((item) => (
          <NavItem key={item.label} item={item} />
        ))}
      </nav>

      <div className="border-t border-sidebar-border px-3 py-4">
        <a
          href={settingsItem?.href ?? "/admin.html"}
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm text-sidebar-foreground/70 hover:bg-sidebar-accent hover:text-sidebar-accent-foreground"
        >
          <Settings className="h-4 w-4" />
          Settings
        </a>
      </div>
    </aside>
  )
}
