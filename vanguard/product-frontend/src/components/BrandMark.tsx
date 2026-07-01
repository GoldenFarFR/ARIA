interface BrandMarkProps {
  className?: string
  size?: number
}

/** Pixel-styled Vanguard shield mark for corporate panels */
export function BrandMark({ className, size = 24 }: BrandMarkProps) {
  return (
    <svg
      viewBox="0 0 40 40"
      width={size}
      height={size}
      className={className}
      aria-hidden
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect x="1" y="1" width="38" height="38" fill="#141416" stroke="#4a4a52" strokeWidth="2" />
      <path
        d="M20 5 L33 10.5 V21.5 C33 28.5 20 35 20 35 C20 35 7 28.5 7 21.5 V10.5 Z"
        fill="#1c1c1f"
        stroke="#a78bfa"
        strokeWidth="1.5"
      />
      <path
        d="M14.5 27 V15.5 H17.2 L20 22.8 L22.8 15.5 H25.5 V27 H23.2 V19.8 L20 27 H20 L16.8 19.8 V27 H14.5 Z"
        fill="#a78bfa"
      />
    </svg>
  )
}