"use client";
import { useQuery } from "@tanstack/react-query";

export default function Settings() {
  const { data } = useQuery({ queryKey: ["s"], queryFn: () => fetch("/api/settings").then((r) => r.json()) });
  const err = (data as any)?.problem;
  return <main>{data?.name}{err?.message}</main>;
}
