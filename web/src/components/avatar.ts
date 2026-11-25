/** 1-2 uppercase letters from the email's local part ("amiel.lejzor@x" gives "AL", "dispatcher@x" gives "D"). */
export function initialsOf(email: string): string {
  const local = email.split("@")[0] ?? "";
  const words = local.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const letters =
    words.length > 1 ? `${words[0]![0]}${words[1]![0]}` : (words[0] ?? "").slice(0, 1);
  return letters.toUpperCase() || "?";
}

/** Same email, same colour. Saturation and lightness are fixed so white text stays readable on every hue, in both themes. */
export function avatarColor(email: string): string {
  let hash = 0;
  for (const ch of email) hash = (hash * 31 + ch.codePointAt(0)!) >>> 0;
  return `hsl(${hash % 360} 55% 32%)`;
}
