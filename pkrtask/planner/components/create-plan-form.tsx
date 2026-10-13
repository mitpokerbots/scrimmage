"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/button";
import { Input } from "@/components/input";
import { createPlan } from "../actions";

export function CreatePlanForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!name.trim() || pending) return;
    setPending(true);
    setError(null);
    const result = await createPlan({ name });
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
        <Button type="submit" disabled={pending || !name.trim()}>
          {pending ? "Creating…" : "Create plan"}
        </Button>
      </form>
      {error ? <p className="text-sm text-status-blocked">{error}</p> : null}
    </div>
  );
}
