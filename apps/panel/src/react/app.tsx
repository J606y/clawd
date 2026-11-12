import { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router";
import { router } from "@/router";
import { MotionRoot } from "@/styles/motion";
import { AUTH_EXPIRED_EVENT, ApiError, api } from "@/lib/api.js";
import { renewCredential } from "@/lib/auth.js";
import { Login } from "@/pages/login/login";

type Phase = "checking" | "login" | "ready" | "unreachable";
const queryClient = new QueryClient({
  defaultOptions: {
    mutations: { retry: false },
  },
});

export function App() {
  const [phase, setPhase] = useState<Phase>("checking");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let active = true;
    const expired = () => {
      queryClient.clear();
      setPhase("login");
    };
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);
    setPhase("checking");
    void api.health().then(() => {
      if (!active) return;
      setPhase("ready");
      void renewCredential();
    }).catch((error: unknown) => {
      if (active) setPhase(error instanceof ApiError && error.kind === "unauthorized" ? "login" : "unreachable");
    });
    return () => {
      active = false;
      window.removeEventListener(AUTH_EXPIRED_EVENT, expired);
    };
  }, [attempt]);

  if (phase === "checking") return <div className="auth-center" aria-busy="true" />;
  if (phase === "login") return <Login onSuccess={() => setPhase("ready")} />;
  if (phase === "unreachable") return <div className="auth-center"><div><div>暂时连不上 clawd。</div><button onClick={() => setAttempt((value) => value + 1)}>重试</button></div></div>;
  return (
    <QueryClientProvider client={queryClient}>
      <MotionRoot>
        <RouterProvider router={router} />
      </MotionRoot>
    </QueryClientProvider>
  );
}
