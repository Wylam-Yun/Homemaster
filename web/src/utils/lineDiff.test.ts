import { describe, expect, it } from 'vitest'

import { diffLines } from './lineDiff'

describe('diffLines', () => {
  it('marks unchanged lines as same', () => {
    expect(diffLines('a\nb', 'a\nb')).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'same', text: 'b' },
    ])
  })

  it('marks added and removed lines around shared context', () => {
    expect(diffLines('a\nold\nc', 'a\nnew\nc')).toEqual([
      { kind: 'same', text: 'a' },
      { kind: 'removed', text: 'old' },
      { kind: 'added', text: 'new' },
      { kind: 'same', text: 'c' },
    ])
  })

  it('handles empty sides', () => {
    // ''.split('\n') yields [''] — the empty line diffs like any other line.
    expect(diffLines('', 'x')).toEqual([
      { kind: 'removed', text: '' },
      { kind: 'added', text: 'x' },
    ])
    expect(diffLines('x', '')).toEqual([
      { kind: 'removed', text: 'x' },
      { kind: 'added', text: '' },
    ])
  })
})
