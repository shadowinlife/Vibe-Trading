import { FlaskConical, Building2, Activity, BarChart3, Bot, CalendarClock, CalendarRange, CandlestickChart, FileText, Layers, LibraryBig, Settings, WalletCards } from "lucide-react";

export const STRATEGY_NAV_ITEMS = [
  { id: "strategies", to: "/strategies", icon: LibraryBig },
  { id: "strategySchedules", to: "/strategy-schedules", icon: CalendarRange },
] as const;

export const NAVIGATION_GROUPS = [
  { id: "research", items: [
    { id: "agent", to: "/", icon: Bot },
    { id: "reports", to: "/reports", icon: FileText },
    { id: "paperTrading", to: "/paper-trading", icon: FlaskConical },
    { id: "portfolio", to: "/portfolio", icon: WalletCards },
  ] },
  { id: "strategy", items: STRATEGY_NAV_ITEMS },
  { id: "analysis", items: [
    { id: "alphaZoo", to: "/alpha-zoo", icon: Layers },
    { id: "optionsLab", to: "/options", icon: CandlestickChart },
    { id: "correlation", to: "/correlation", icon: BarChart3 },
  ] },
  { id: "automation", items: [
    { id: "pixelOffice", to: "/pixel-office", icon: Building2 },
    { id: "scheduled", to: "/scheduled", icon: CalendarClock },
    { id: "runtime", to: "/runtime", icon: Activity },
    { id: "settings", to: "/settings", icon: Settings },
  ] },
] as const;

export type NavigationItem = typeof NAVIGATION_GROUPS[number]["items"][number];

export function isNavigationActive(to: string, pathname: string): boolean {
  return to === "/" ? pathname === "/" || pathname === "/agent"
    : pathname === to || pathname.startsWith(`${to}/`);
}
