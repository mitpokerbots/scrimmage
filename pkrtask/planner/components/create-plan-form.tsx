"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/button";
import { Input, Select } from "@/components/input";
import { createPlan } from "../actions";
import type { PlanVisibility } from "../types";

export function CreatePlanForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [visibility, setVisibility] = useState<PlanVisibility>("shared");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!name.trim() || pending) return;
    setPending(true);
    setError(null);
    const result = await createPlan({ name, visibility });
    setPending(false);
    if (result.ok) {
      router.push(`/plans/${result.data.planId}`);
    } else {
      setError(result.error);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="New plan name (e.g. Spring Season)"
          aria-label="Plan name"
        />
        <div className="w-52 shrink-0">
          <Select
            value={visibility}
            onChange={(e) => setVisibility(e.target.value as PlanVisibility)}
            aria-label="Who can see it"
          >
            <option value="shared">Shared with everyone</option>
            <option value="private">Private (only me)</option>
          </Select>
        </div>
        <Button type="submit" disabled={pending || !name.trim()}>
          {pending ? "Creating…" : "Create plan"}
        </Button>
      </form>
      {error ? <p className="text-sm text-status-blocked">{error}</p> : null}
    </div>
  );
}
