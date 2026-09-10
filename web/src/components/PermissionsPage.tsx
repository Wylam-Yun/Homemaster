import { useCallback, useEffect, useRef, useState } from 'react'

import type { Grant } from '../api/http'
import styles from './PermissionsPage.module.css'

export type GrantsApi = {
  listGrants(params: {
    resource_kind?: 'object' | 'area'
    status?: string
    cursor?: string | null
    limit?: number
  }): Promise<{ grants: Grant[]; next_cursor: string | null }>
  revokeGrant(
    grantId: string,
    revocation: { submission_id: string; expected_revision: number },
  ): Promise<Grant>
}

type Props = {
  api: GrantsApi
  refreshSignal: number
}

type KindTab = 'object' | 'area'

type RevokeOperation = {
  grantId: string
  submissionId: string
  expectedRevision: number
}

function prepareRevoke(grant: Grant): RevokeOperation {
  return {
    grantId: grant.grant_id,
    submissionId: crypto.randomUUID(),
    expectedRevision: grant.revision,
  }
}

const PAGE_SIZE = 50

function grantTitle(grant: Grant): string {
  return grant.display_name ?? grant.resource_id
}

function grantPlace(grant: Grant): string {
  return grant.location ?? grant.environment_id
}

function grantAction(grant: Grant): string {
  return grant.action_label ?? grant.action
}

function groupByAction(grants: Grant[]): Array<{ action: string; grants: Grant[] }> {
  const groups = new Map<string, Grant[]>()
  for (const grant of grants) {
    const key = grantAction(grant)
    const list = groups.get(key) ?? []
    list.push(grant)
    groups.set(key, list)
  }
  return [...groups.entries()].map(([action, items]) => ({ action, grants: items }))
}

export function PermissionsPage({ api, refreshSignal }: Props) {
  const [kind, setKind] = useState<KindTab>('object')
  const [active, setActive] = useState<Grant[]>([])
  const [activeCursor, setActiveCursor] = useState<string | null>(null)
  const [revoked, setRevoked] = useState<Grant[]>([])
  const [revokedCursor, setRevokedCursor] = useState<string | null>(null)
  const [revokedOpen, setRevokedOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [revokingId, setRevokingId] = useState<string | null>(null)
  const [failure, setFailure] = useState<{ message: string; operation: RevokeOperation } | null>(null)
  const pendingOperation = useRef<RevokeOperation | null>(null)

  const loadPage = useCallback(async (
    status: 'active' | 'revoked',
    cursor: string | null,
    append: boolean,
  ): Promise<void> => {
    const page = await api.listGrants({
      resource_kind: kind,
      status,
      cursor,
      limit: PAGE_SIZE,
    })
    if (status === 'active') {
      setActive(previous => (append ? [...previous, ...page.grants] : page.grants))
      setActiveCursor(page.next_cursor)
    } else {
      setRevoked(previous => (append ? [...previous, ...page.grants] : page.grants))
      setRevokedCursor(page.next_cursor)
    }
  }, [api, kind])

  const reloadAll = useCallback(async (): Promise<void> => {
    setLoading(true)
    try {
      await loadPage('active', null, false)
      if (revokedOpen) await loadPage('revoked', null, false)
    } finally {
      setLoading(false)
    }
  }, [loadPage, revokedOpen])

  useEffect(() => {
    void reloadAll()
  }, [reloadAll, refreshSignal])

  const submitRevoke = useCallback(async (operation: RevokeOperation): Promise<void> => {
    pendingOperation.current = operation
    setRevokingId(operation.grantId)
    setFailure(null)
    try {
      await api.revokeGrant(operation.grantId, {
        submission_id: operation.submissionId,
        expected_revision: operation.expectedRevision,
      })
      pendingOperation.current = null
      await reloadAll()
    } catch (error) {
      setFailure({
        message: error instanceof Error ? error.message : 'Revoke failed.',
        operation,
      })
    } finally {
      setRevokingId(null)
    }
  }, [api, reloadAll])

  const groups = groupByAction(active)

  return (
    <section className={styles.page} aria-labelledby="permissions-page-title">
      <div className={styles.headingRow}>
        <div>
          <p className={styles.eyebrow}>长期权限</p>
          <h1 id="permissions-page-title">权限管理</h1>
          <p>查看长期有效的授权并按动作撤销。区域权限只检查目的地，不限制途经区域。</p>
        </div>
        <button type="button" className={styles.refresh} onClick={() => { void reloadAll() }} disabled={loading}>
          {loading ? '加载中…' : '刷新'}
        </button>
      </div>

      {failure !== null && (
        <div className={styles.error} role="alert">
          <div><strong>撤销失败</strong><span>{failure.message}</span></div>
          <button type="button" onClick={() => { void submitRevoke(failure.operation) }}>重试</button>
        </div>
      )}

      <div className={styles.tabs} role="tablist" aria-label="资源类型">
        <button
          type="button"
          role="tab"
          aria-selected={kind === 'object'}
          onClick={() => { setKind('object') }}
        >
          物品动作
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={kind === 'area'}
          onClick={() => { setKind('area') }}
        >
          目的地区域
        </button>
      </div>

      {active.length === 0 && !loading && (
        <div className={styles.empty}>暂无长期权限：新的申请在批准后会显示在这里。</div>
      )}

      {groups.map(group => (
        <section key={group.action} aria-label={group.action}>
          <h2 className={styles.groupTitle}>{group.action}</h2>
          <ul className={styles.list}>
            {group.grants.map(grant => (
              <li key={grant.grant_id} className={styles.item}>
                <div>
                  <strong>{grantTitle(grant)}</strong>
                  <span className={styles.place}>{grantPlace(grant)}</span>
                  <span className={styles.meta}>已允许{grantAction(grant)} · {grant.created_at}</span>
                </div>
                <button
                  type="button"
                  aria-label={`撤销${grantTitle(grant)}的${grantAction(grant)}`}
                  disabled={revokingId !== null}
                  onClick={() => { void submitRevoke(prepareRevoke(grant)) }}
                >
                  {revokingId === grant.grant_id ? '撤销中…' : `撤销${grantAction(grant)}`}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}

      {activeCursor !== null && (
        <button type="button" onClick={() => { void loadPage('active', activeCursor, true) }}>
          加载更多
        </button>
      )}

      <details
        open={revokedOpen}
        onToggle={event => { setRevokedOpen((event.target as HTMLDetailsElement).open) }}
      >
        <summary>已撤销</summary>
        {revoked.length === 0 ? (
          <div className={styles.empty}>暂无已撤销记录。</div>
        ) : (
          <ul className={styles.list}>
            {revoked.map(grant => (
              <li key={grant.grant_id} className={styles.item}>
                <div>
                  <strong>{grantTitle(grant)}</strong>
                  <span className={styles.place}>{grantPlace(grant)}</span>
                  <span className={styles.meta}>已撤销{grantAction(grant)} · {grant.revoked_at ?? ''}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
        {revokedCursor !== null && (
          <button type="button" onClick={() => { void loadPage('revoked', revokedCursor, true) }}>
            加载更多
          </button>
        )}
      </details>
    </section>
  )
}
