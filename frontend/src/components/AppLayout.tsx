import { ActionIcon, AppShell, Box, Burger, Group, ScrollArea, Stack, Text, Tooltip } from "@mantine/core";
import { useDisclosure, useMediaQuery } from "@mantine/hooks";
import {
  BarChart3,
  ChefHat,
  ChevronLeft,
  ChevronRight,
  Download,
  Flame,
  Home,
  Leaf,
  type LucideIcon,
  Package,
  PanelRight,
  Receipt,
  ClipboardCheck,
  ScanBarcode,
  ScanLine,
  Settings,
  ShoppingBag,
  ShoppingCart,
  Utensils,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Link, Outlet, useLocation } from "react-router-dom";

import { ROUTES } from "../routes/paths";
import { ActionConsoleProvider } from "./ActionConsole";
import { FeedbackButton } from "./FeedbackButton";
import { ProfilePicker } from "./ProfilePicker";
import { StatusToast } from "./StatusToast";

type NavItem = { label: string; to: string; icon: LucideIcon };
type NavGroup = { section: string; items: NavItem[] };

const NAV_GROUPS: NavGroup[] = [
  {
    section: "Kitchen",
    items: [
      { label: "Dashboard", to: ROUTES.dashboard, icon: Home },
      // The everyday surface (ADR 0023): recording what was used belongs beside
      // the pantry itself, not filed under Intake with the check-in tools.
      { label: "Track", to: ROUTES.track, icon: ClipboardCheck },
      { label: "Inventory", to: ROUTES.inventory, icon: Package },
      { label: "Shopping List", to: ROUTES.shoppingList, icon: ShoppingCart },
      { label: "Recipes", to: ROUTES.recipes, icon: ChefHat },
    ],
  },
  {
    section: "Meals",
    items: [
      { label: "Log Meal", to: ROUTES.logMeal, icon: Utensils },
      { label: "Calories", to: ROUTES.calories, icon: Flame },
    ],
  },
  {
    section: "Intake",
    items: [
      { label: "Scan Station", to: ROUTES.scanStation, icon: ScanLine },
      { label: "Scan", to: ROUTES.scan, icon: ScanBarcode },
      { label: "Ingest", to: ROUTES.ingest, icon: Download },
      { label: "Receipts", to: ROUTES.emailReceipts, icon: Receipt },
      { label: "Purchases", to: ROUTES.purchases, icon: ShoppingBag },
    ],
  },
];

// Rendered at the bottom of the sidebar, below a divider.
const BOTTOM_ITEMS: NavItem[] = [
  { label: "Insights", to: ROUTES.insights, icon: BarChart3 },
  { label: "Settings", to: ROUTES.settings, icon: Settings },
];

const SIDEBAR_STORAGE_KEY = "pantry_sidebar_collapsed";

function isActivePath(currentPathname: string, targetPath: string): boolean {
  if (targetPath === ROUTES.dashboard) {
    return currentPathname === ROUTES.dashboard;
  }
  return currentPathname === targetPath || currentPathname.startsWith(`${targetPath}/`);
}

function getInitialCollapsed(): boolean {
  if (typeof window === "undefined") return false;
  return window.localStorage.getItem(SIDEBAR_STORAGE_KEY) === "true";
}

export function AppLayout() {
  const location = useLocation();
  const isDesktop = useMediaQuery("(min-width: 62em)", true);
  // No auto-select: a null selectedLocationId means "All locations", which is the
  // default view. The location selector lives in page headers / the console.

  const [collapsed, setCollapsed] = useState<boolean>(() => getInitialCollapsed());
  const [mobileNavOpened, mobileNav] = useDisclosure(false);
  const [mobileConsoleOpened, mobileConsole] = useDisclosure(false);

  // The page-owned Action Console reports whether it is present, and the shell
  // exposes the DOM container it portals into.
  const [hasConsole, setHasConsole] = useState(false);
  const [consoleContainer, setConsoleContainer] = useState<HTMLElement | null>(null);
  const consoleRef = useCallback((node: HTMLElement | null) => setConsoleContainer(node), []);

  const railMode = isDesktop && collapsed;

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      if (typeof window !== "undefined") {
        window.localStorage.setItem(SIDEBAR_STORAGE_KEY, String(next));
      }
      return next;
    });
  };

  // Close the mobile drawers on navigation.
  useEffect(() => {
    mobileNav.close();
    mobileConsole.close();
  }, [location.pathname]); // eslint-disable-line react-hooks/exhaustive-deps

  // Never leave a stale mobile-console drawer open once its page is gone.
  useEffect(() => {
    if (!hasConsole) mobileConsole.close();
  }, [hasConsole]); // eslint-disable-line react-hooks/exhaustive-deps

  const renderExpandedGroup = (group: NavGroup) => (
    <Box key={group.section}>
      <div className="pantry-kicker">{group.section}</div>
      {group.items.map((item) => (
        <NavItemLink key={item.to} item={item} active={isActivePath(location.pathname, item.to)} onNavigate={mobileNav.close} />
      ))}
    </Box>
  );

  return (
    <AppShell
      header={{ height: 56, collapsed: isDesktop, offset: true }}
      navbar={{
        width: { base: 240, md: collapsed ? 76 : 240 },
        breakpoint: "md",
        collapsed: { mobile: !mobileNavOpened, desktop: false },
      }}
      aside={{
        width: 334,
        breakpoint: "md",
        collapsed: { desktop: !hasConsole, mobile: !mobileConsoleOpened },
      }}
      // Pad the Main section so every page (not just the kiosk Dashboard/Inventory,
      // which used to self-pad) gets breathing room from the page edges.
      padding="lg"
    >
      <AppShell.Header style={{ background: "var(--pantry-surface)", borderColor: "var(--pantry-divider)" }}>
        <Group h="100%" px="md" justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap">
            <Burger opened={mobileNavOpened} onClick={mobileNav.toggle} size="sm" aria-label="Toggle navigation" />
            <Group gap={8} wrap="nowrap">
              <Leaf size={22} color="var(--pantry-accent)" strokeWidth={2.4} />
              <Text style={{ fontFamily: "var(--mantine-heading-font-family)", fontSize: 20 }}>PantryOS</Text>
            </Group>
          </Group>
          <Group gap="xs" wrap="nowrap">
            <ProfilePicker />
            {hasConsole ? (
              <ActionIcon
                variant="default"
                size="lg"
                radius="xl"
                onClick={mobileConsole.toggle}
                aria-label="Toggle action console"
              >
                <PanelRight size={18} />
              </ActionIcon>
            ) : null}
          </Group>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar style={{ background: "var(--pantry-surface)", borderColor: "var(--pantry-divider)" }}>
        {railMode ? (
          <Stack align="center" gap={5} py="md" h="100%">
            <Box
              style={{
                width: 40,
                height: 40,
                borderRadius: 12,
                background: "var(--pantry-accent)",
                display: "grid",
                placeItems: "center",
                marginBottom: 8,
              }}
            >
              <Leaf size={22} color="#fff" strokeWidth={2.6} />
            </Box>
            <Tooltip label="Expand sidebar" position="right">
              <ActionIcon variant="default" size="md" radius="xl" onClick={toggleCollapsed} aria-label="Expand sidebar" mb={6}>
                <ChevronRight size={17} />
              </ActionIcon>
            </Tooltip>
            {NAV_GROUPS.flatMap((g) => g.items).map((item) => (
              <RailItem key={item.to} item={item} active={isActivePath(location.pathname, item.to)} />
            ))}
            <Box style={{ width: 36, height: 1, background: "var(--pantry-divider)", margin: "6px 0" }} />
            {BOTTOM_ITEMS.map((item, idx) => (
              <RailItem
                key={item.to}
                item={item}
                active={isActivePath(location.pathname, item.to)}
                style={idx === BOTTOM_ITEMS.length - 1 ? { marginTop: "auto" } : undefined}
              />
            ))}
          </Stack>
        ) : (
          <Stack gap={0} p={14} h="100%">
            <Group gap={9} px={6} pb={16} wrap="nowrap">
              <Leaf size={24} color="var(--pantry-accent)" strokeWidth={2.4} />
              <Text style={{ fontFamily: "var(--mantine-heading-font-family)", fontSize: 20, marginRight: "auto" }}>
                PantryOS
              </Text>
              {isDesktop ? (
                <Tooltip label="Collapse sidebar" position="right">
                  <ActionIcon variant="default" size="md" radius="xl" onClick={toggleCollapsed} aria-label="Collapse sidebar">
                    <ChevronLeft size={17} />
                  </ActionIcon>
                </Tooltip>
              ) : null}
            </Group>
            <Box px={6} pb={12}>
              <ProfilePicker />
            </Box>
            <ScrollArea style={{ flex: 1 }} type="never">
              {NAV_GROUPS.map(renderExpandedGroup)}
            </ScrollArea>
            <Box pt={8}>
              <Box style={{ height: 1, background: "var(--pantry-divider)", margin: "8px 6px" }} />
              {BOTTOM_ITEMS.map((item) => (
                <NavItemLink
                  key={item.to}
                  item={item}
                  active={isActivePath(location.pathname, item.to)}
                  onNavigate={mobileNav.close}
                />
              ))}
            </Box>
          </Stack>
        )}
      </AppShell.Navbar>

      <AppShell.Aside style={{ background: "var(--pantry-surface)", borderColor: "var(--pantry-divider)" }}>
        <Box ref={consoleRef} h="100%" style={{ display: "flex", flexDirection: "column", overflowY: "auto" }} />
      </AppShell.Aside>

      <AppShell.Main>
        <ActionConsoleProvider container={consoleContainer} setActive={setHasConsole}>
          <Outlet />
        </ActionConsoleProvider>
        <FeedbackButton />
        <StatusToast />
      </AppShell.Main>
    </AppShell>
  );
}

function NavItemLink({ item, active, onNavigate }: { item: NavItem; active: boolean; onNavigate: () => void }) {
  const Icon = item.icon;
  return (
    <Link className="pantry-navitem" data-active={active} to={item.to} onClick={onNavigate}>
      <Icon size={20} strokeWidth={2.4} />
      {item.label}
    </Link>
  );
}

function RailItem({
  item,
  active,
  style,
}: {
  item: NavItem;
  active: boolean;
  style?: React.CSSProperties;
}) {
  const Icon = item.icon;
  return (
    <Tooltip label={item.label} position="right">
      <Link className="pantry-railitem" data-active={active} to={item.to} style={style} aria-label={item.label}>
        <Icon size={21} strokeWidth={2.4} />
      </Link>
    </Tooltip>
  );
}
