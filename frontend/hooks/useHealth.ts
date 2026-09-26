import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

interface HealthStatus {
  status: string;
  service: string;
  git_commit: string | null;
}

/** The backend's own report of what it's running — specifically the git commit its
 * process was started from, so Settings can show it next to the frontend's build and
 * make a stale (not-yet-restarted) backend visible instead of silent. */
export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: async () => (await api.get<HealthStatus>("/api/health")).data,
    refetchInterval: 60_000,
  });
}
