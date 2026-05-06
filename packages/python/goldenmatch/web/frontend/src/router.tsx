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
    <div className="min-h-screen">
      <header className="border-b px-6 py-3 flex gap-4 text-sm">
        <Link to="/" className="font-semibold">
          GoldenMatch
        </Link>
        <Link to="/" className="hover:underline">
          Home
        </Link>
        <Link to="/workbench" className="hover:underline">
          Workbench
        </Link>
      </header>
      <Outlet />
    </div>
  ),
});

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
