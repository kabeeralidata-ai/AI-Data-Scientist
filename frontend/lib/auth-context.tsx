"use client";

import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { api, clearToken, getErrorMessage, getToken, setToken } from "@/lib/api";
import type { User } from "@/types";

interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();

  const fetchMe = useCallback(async () => {
    const token = getToken();
    if (!token) {
      setIsLoading(false);
      return;
    }
    try {
      const res = await api.get<User>("/api/auth/me");
      setUser(res.data);
    } catch {
      clearToken();
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchMe();
  }, [fetchMe]);

  const login = useCallback(async (email: string, password: string) => {
    try {
      const res = await api.post<{ access_token: string }>("/api/auth/login", { email, password });
      setToken(res.data.access_token);
      await fetchMe();
    } catch (error) {
      throw new Error(getErrorMessage(error, "Invalid email or password."));
    }
  }, [fetchMe]);

  const register = useCallback(async (name: string, email: string, password: string) => {
    try {
      const res = await api.post<{ access_token: string }>("/api/auth/register", { name, email, password });
      setToken(res.data.access_token);
      await fetchMe();
    } catch (error) {
      throw new Error(getErrorMessage(error, "Could not create your account."));
    }
  }, [fetchMe]);

  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    router.push("/login");
  }, [router]);

  return (
    <AuthContext.Provider value={{ user, isLoading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
