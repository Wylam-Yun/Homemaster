export type DiffLine = { kind: 'same' | 'added' | 'removed'; text: string }

/**
 * LCS line diff — SKILL.md drafts are small enough for O(n·m).
 */
export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n')
  const b = after.split('\n')
  const rows = a.length + 1
  const cols = b.length + 1
  const table: number[][] = Array.from({ length: rows }, () => new Array<number>(cols).fill(0))
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1])
    }
  }
  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] })
      i += 1
      j += 1
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      out.push({ kind: 'removed', text: a[i] })
      i += 1
    } else {
      out.push({ kind: 'added', text: b[j] })
      j += 1
    }
  }
  while (i < a.length) { out.push({ kind: 'removed', text: a[i] }); i += 1 }
  while (j < b.length) { out.push({ kind: 'added', text: b[j] }); j += 1 }
  return out
}
