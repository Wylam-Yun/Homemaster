import { render, screen, waitFor } from '@testing-library/react'
import { fireEvent } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { Grant } from '../api/http'
import { PermissionsPage, type GrantsApi } from './PermissionsPage'

function grant(overrides: Partial<Grant> & { grant_id: string }): Grant {
  return {
    environment_id: 'home',
    resource_kind: 'object',
    resource_id: 'cup-a',
    action: 'pick_up',
    created_at: '2026-09-10T01:00:00Z',
    created_by: 'tester',
    source_request_id: 'request-1',
    source_item_id: 'item-1',
    revoked_at: null,
    revoked_by: null,
    revision: 1,
    status: 'active',
    display_name: '白色杯子',
    location: '卧室床头柜',
    action_label: '拿取',
    ...overrides,
  }
}

const pickGrant = grant({ grant_id: 'grant-pick-1', source_item_id: 'item-pick-1' })
const placeGrant = grant({
  grant_id: 'grant-place-1',
  resource_id: 'cup-a',
  action: 'place',
  source_item_id: 'item-place-1',
  display_name: '白色杯子',
  location: '卧室床头柜',
  action_label: '放置',
})
const twinGrant = grant({
  grant_id: 'grant-pick-2',
  resource_id: 'cup-c',
  action: 'pick_up',
  source_request_id: 'request-2',
  source_item_id: 'item-pick-2',
  display_name: '白色杯子',
  location: '客厅茶几',
})
const areaGrant = grant({
  grant_id: 'grant-enter-1',
  resource_kind: 'area',
  resource_id: 'bedroom',
  action: 'enter',
  source_item_id: 'item-enter-1',
  display_name: '卧室',
  location: '卧室',
  action_label: '进入',
})

function makeApi(grants: Grant[], onRevoke?: (grantId: string) => void): GrantsApi & {
  listGrants: ReturnType<typeof vi.fn>
  revokeGrant: ReturnType<typeof vi.fn>
} {
  return {
    listGrants: vi.fn(async (params: { resource_kind?: string; status?: string }) => ({
      grants: grants.filter(item =>
        (params.resource_kind === undefined || item.resource_kind === params.resource_kind)
        && (params.status === undefined || params.status === 'active'
          ? item.revoked_at === null
          : item.revoked_at !== null),
      ),
      next_cursor: null,
    })),
    revokeGrant: vi.fn(async (grantId: string) => {
      const target = grants.find(item => item.grant_id === grantId)
      if (target === undefined) throw new Error(`unknown grant ${grantId}`)
      onRevoke?.(grantId)
      const revoked: Grant = { ...target, revoked_at: '2026-09-10T02:00:00Z', status: 'revoked' }
      return revoked
    }),
  }
}

describe('PermissionsPage', () => {
  it('groups object grants by action and revokes one without touching the other', async () => {
    const live = [pickGrant, placeGrant]
    const api = makeApi(live, grantId => {
      const index = live.findIndex(item => item.grant_id === grantId)
      if (index >= 0) live.splice(index, 1)
    })
    render(<PermissionsPage api={api} refreshSignal={0} />)

    expect(await screen.findByText('已允许拿取 · 2026-09-10T01:00:00Z')).toBeVisible()
    expect(screen.getByText('拿取')).toBeVisible()
    expect(screen.getByText('放置')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: '撤销白色杯子的拿取' }))
    await waitFor(() => {
      expect(api.revokeGrant).toHaveBeenCalledWith('grant-pick-1', {
        submission_id: expect.any(String),
        expected_revision: 1,
      })
    })
    await waitFor(() => { expect(screen.queryByText('已允许拿取 · 2026-09-10T01:00:00Z')).toBeNull() })
    expect(screen.queryByText('拿取')).toBeNull()
    expect(screen.getByText('放置')).toBeVisible()
    expect(api.listGrants.mock.calls.length).toBeGreaterThan(1)
  })

  it('shows locations for same-name grants and never internal ids', async () => {
    const api = makeApi([pickGrant, twinGrant])
    render(<PermissionsPage api={api} refreshSignal={0} />)

    expect(await screen.findByText('卧室床头柜')).toBeVisible()
    expect(screen.getByText('客厅茶几')).toBeVisible()
    const text = document.body.textContent ?? ''
    for (const secret of ['grant-pick-1', 'grant-pick-2', 'cup-a', 'cup-c', 'item-pick-1', 'request-1']) {
      expect(text).not.toContain(secret)
    }
  })

  it('explains destination-only areas and lists the area tab', async () => {
    const api = makeApi([pickGrant, areaGrant])
    render(<PermissionsPage api={api} refreshSignal={0} />)

    expect(await screen.findByText(/区域权限只检查目的地，不限制途经区域/)).toBeVisible()
    fireEvent.click(screen.getByRole('tab', { name: '目的地区域' }))
    expect(await screen.findByText('已允许进入 · 2026-09-10T01:00:00Z')).toBeVisible()
    expect(screen.queryByText('白色杯子')).toBeNull()
  })

  it('shows an accurate empty state without records', async () => {
    const api = makeApi([])
    render(<PermissionsPage api={api} refreshSignal={0} />)

    expect(await screen.findByText(/暂无长期权限/)).toBeVisible()
  })

  it('pages through the server cursor instead of guessing', async () => {
    const listGrants = vi.fn()
      .mockResolvedValueOnce({ grants: [pickGrant], next_cursor: 'grant-pick-1' })
      .mockResolvedValueOnce({ grants: [placeGrant], next_cursor: null })
    const api: GrantsApi = {
      listGrants,
      revokeGrant: vi.fn(),
    }
    render(<PermissionsPage api={api} refreshSignal={0} />)

    expect(await screen.findByText('已允许拿取 · 2026-09-10T01:00:00Z')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '加载更多' }))
    await waitFor(() => {
      expect(listGrants).toHaveBeenLastCalledWith(expect.objectContaining({ cursor: 'grant-pick-1' }))
    })
    expect(await screen.findByText('已允许放置 · 2026-09-10T01:00:00Z')).toBeVisible()
  })

  it('reuses the locked submission id when retrying a failed revoke', async () => {
    const revokeGrant = vi.fn()
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValueOnce({ ...pickGrant, revoked_at: '2026-09-10T02:00:00Z', status: 'revoked' as const })
    const api: GrantsApi = { listGrants: makeApi([pickGrant]).listGrants, revokeGrant }
    render(<PermissionsPage api={api} refreshSignal={0} />)

    expect(await screen.findByText('已允许拿取 · 2026-09-10T01:00:00Z')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '撤销白色杯子的拿取' }))
    expect(await screen.findByRole('button', { name: '重试' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))

    await waitFor(() => { expect(revokeGrant).toHaveBeenCalledTimes(2) })
    const first = revokeGrant.mock.calls[0]![1] as { submission_id: string }
    const second = revokeGrant.mock.calls[1]![1] as { submission_id: string }
    expect(first.submission_id).toBe(second.submission_id)
  })

  it('reloads from the backend when a grant change arrives', async () => {
    const api = makeApi([pickGrant])
    const { rerender } = render(<PermissionsPage api={api} refreshSignal={0} />)

    expect(await screen.findByText('已允许拿取 · 2026-09-10T01:00:00Z')).toBeVisible()
    const calls = api.listGrants.mock.calls.length
    rerender(<PermissionsPage api={api} refreshSignal={1} />)
    await waitFor(() => {
      expect(api.listGrants.mock.calls.length).toBeGreaterThan(calls)
    })
  })
})
