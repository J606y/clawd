import { lazy, Suspense, type ReactNode } from "react";
import { createHashRouter, Navigate, useParams } from "react-router";
import { isSettingsTab } from "@/routes";
import { Shell } from "@/shell/shell";

const ImagePage = lazy(() => import("@/pages/image/image-page").then((m) => ({ default: m.ImagePage })));
const FilesPage = lazy(() => import("@/pages/files/files-page").then((m) => ({ default: m.FilesPage })));
const ActivityPage = lazy(() => import("@/pages/activity/activity-page").then((m) => ({ default: m.ActivityPage })));
const StatusPage = lazy(() => import("@/pages/status/status-page").then((m) => ({ default: m.StatusPage })));
const SettingsPage = lazy(() => import("@/pages/settings/settings-page").then((m) => ({ default: m.SettingsPage })));
const KanbanPage = lazy(() => import("@/pages/kanban/kanban-page").then((m) => ({ default: m.KanbanPage })));

function Loading() {
  return <p className="page">加载中</p>;
}

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<Loading />}>{children}</Suspense>;
}

function ChatMarker() {
  return null;
}

function AdminRedirect() {
  const { tab } = useParams();
  const next = isSettingsTab(tab) ? `/settings/${tab}` : "/settings/config";
  return <Navigate to={next} replace />;
}

function RouteError() {
  return <p className="page" role="alert">页面无法打开</p>;
}

export const router = createHashRouter([
  {
    path: "/",
    element: <Shell />,
    errorElement: <RouteError />,
    children: [
      { index: true, element: <ChatMarker /> },
      { path: "image", element: <Lazy><ImagePage /></Lazy> },
      { path: "image/*", element: <Lazy><ImagePage /></Lazy> },
      { path: "files", element: <Lazy><FilesPage /></Lazy> },
      { path: "files/*", element: <Lazy><FilesPage /></Lazy> },
      { path: "activity", element: <Lazy><ActivityPage /></Lazy> },
      { path: "activity/*", element: <Lazy><ActivityPage /></Lazy> },
      { path: "kanban", element: <Lazy><KanbanPage /></Lazy> },
      { path: "kanban/*", element: <Lazy><KanbanPage /></Lazy> },
      { path: "status", element: <Lazy><StatusPage /></Lazy> },
      { path: "status/*", element: <Lazy><StatusPage /></Lazy> },
      { path: "settings", element: <Lazy><SettingsPage /></Lazy> },
      { path: "settings/:tab", element: <Lazy><SettingsPage /></Lazy> },
      { path: "settings/:tab/*", element: <Lazy><SettingsPage /></Lazy> },
      { path: "jobs", element: <Navigate to="/settings/jobs" replace /> },
      { path: "skills", element: <Navigate to="/settings/skills" replace /> },
      { path: "admin", element: <Navigate to="/settings/config" replace /> },
      { path: "admin/:tab", element: <AdminRedirect /> },
      { path: "admin/:tab/*", element: <AdminRedirect /> },
      { path: "*", element: <Navigate to="/" replace /> },
    ],
  },
]);
