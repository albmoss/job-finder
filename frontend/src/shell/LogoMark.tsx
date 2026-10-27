export function LogoMark({ size = 28 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      aria-hidden="true"
    >
      <path
        d="M3 8.5V6A3 3 0 0 1 6 3H8.5M15.5 3H18A3 3 0 0 1 21 6V8.5M21 15.5V18A3 3 0 0 1 18 21H15.5M8.5 21H6A3 3 0 0 1 3 18V15.5"
        strokeWidth={2.2}
      />
      <path d="M8.5 10.5H15.5" strokeWidth={2.4} />
      <path d="M8.5 14.25H15.5" stroke="var(--track)" strokeWidth={1.6} />
      <path d="M8.5 14.25H13.25" strokeWidth={1.6} />
    </svg>
  );
}
