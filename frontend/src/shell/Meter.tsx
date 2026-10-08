export function Meter({ value, className }: { value: number | null; className?: string }) {
  const known = value != null && Number.isFinite(value);
  const fraction = known ? Math.min(1, Math.max(0, value)) : 0;
  return (
    <span
      className={`meter${known ? '' : ' is-indeterminate'}${className ? ` ${className}` : ''}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={known ? Math.round(fraction * 100) : undefined}
    >
      <span className="meter-fill" style={known ? { transform: `scaleX(${fraction})` } : undefined} />
    </span>
  );
}
