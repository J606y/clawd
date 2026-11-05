import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "react-router";
import { router } from "@/router";
import { MotionRoot } from "@/styles/motion";

const queryClient = new QueryClient({
  defaultOptions: {
    mutations: { retry: false },
  },
});

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <MotionRoot>
        <RouterProvider router={router} />
      </MotionRoot>
    </QueryClientProvider>
  );
}
