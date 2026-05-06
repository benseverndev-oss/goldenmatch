import {
  createRootRoute,
  createRoute,
  createRouter,
  Link,
  Outlet,
} from "@tanstack/react-router";
import { Home } from "./routes/Home";
import { Inspector } from "./routes/Inspector";
import { Workbench } from "./routes/Workbench";

const rootRoute = createRootRoute({
  component: () => (
    <div className="min-h-screen flex flex-col">
      {/* top frame strip — echoes the social-preview wordmark frame */}
      <div aria-hidden className="h-[2px] frame-strip" />

      <header className="px-8 py-5 flex items-end gap-8 border-b border-ink-800">
        <Link to="/" className="group flex items-baseline gap-3 select-none">
          <span className="display text-3xl text-gold-300 leading-none group-hover:text-gold-200 transition-colors">
            GoldenMatch
          </span>
          <span className="eyebrow text-ink-500 group-hover:text-ink-400 transition-colors">
            entity resolution&nbsp;·&nbsp;workbench
          </span>
        </Link>

        <nav className="ml-auto flex items-center gap-1 text-[12px]">
          <NavLink to="/">Project</NavLink>
          <NavLink to="/workbench">Workbench</NavLink>
        </nav>
      </header>

      <main className="flex-1 min-h-0">
        <Outlet />
      </main>

      <div aria-hidden className="h-[2px] frame-strip" />
    </div>
  ),
});

function NavLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link
      to={to}
      className="px-3 py-2 uppercase tracking-eyebrow text-ink-400 hover:text-gold-200 transition-colors"
      activeProps={{ className: "text-gold-300" }}
    >
      {children}
    </Link>
  );
}

const homeRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  component: Home,
});

const inspectorRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/runs/$name",
  component: Inspector,
});

const workbenchRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/workbench",
  component: Workbench,
});

const routeTree = rootRoute.addChildren([
  homeRoute,
  inspectorRoute,
  workbenchRoute,
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
