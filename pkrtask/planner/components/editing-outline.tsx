const EDITOR_COLORS = ["#f2994a", "#bb6bd9", "#56ccf2", "#6fcf97", "#eb5757", "#f2c94c", "#9b8cff", "#2fd3b5"];

/** A stable color per person, so the same editor looks the same everywhere. */
export function editorColor(kerberos: string): string {
  let hash = 0;
  for (const char of kerberos) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return EDITOR_COLORS[hash % EDITOR_COLORS.length]!;
}

/**
 * Outline and name tag over a task or sub-plan that someone else is editing.
 * `scale` is the node's nesting scale, so the tag stays proportionate inside frames.
 */
export function EditingOutline({
  editor,
  scale,
  radius,
}: {
  editor: { kerberos: string; name: string };
  scale: number;
  radius: number;
}) {
  const color = editorColor(editor.kerberos);
  return (
    <div
      className="pointer-events-none absolute inset-0 z-20"
      style={{ borderRadius: radius, boxShadow: `0 0 0 ${2 * scale}px ${color}` }}
      aria-label={`${editor.name} is editing`}
    >
      <span
        className="absolute left-0 whitespace-nowrap font-semibold text-night"
        style={{
          bottom: "100%",
          marginBottom: 2 * scale,
          padding: `${1 * scale}px ${5 * scale}px`,
          fontSize: 11 * scale,
          borderRadius: 3 * scale,
          backgroundColor: color,
        }}
      >
        {editor.name}
      </span>
    </div>
  );
}
