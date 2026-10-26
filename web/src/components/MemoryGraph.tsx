import { useEffect, useMemo, useRef } from 'react'
import { drag, type D3DragEvent } from 'd3-drag'
import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import { select } from 'd3-selection'
import { zoom, zoomIdentity, type ZoomBehavior, type D3ZoomEvent } from 'd3-zoom'

import type { MemoryGraph as MemoryGraphData } from '../api/http'
import styles from './MemoryGraph.module.css'

/** Palette shared with the page legend; unknown types fall through to muted. */
export const TYPE_COLORS: Record<string, string> = {
  fact: '#63e6be',
  experience: '#f2c66d',
  episodic: '#6aa8ff',
  procedure: '#7dd3fc',
  trajectory: '#c4b5fd',
  alfworld_experience: '#5ee6a8',
  skill_candidate: '#fda4af',
  tool_trace: '#94a3b8',
  profile: '#f0abfc',
}
const FALLBACK_COLOR = '#a8b2c0'

export function typeColor(memoryType: string): string {
  return TYPE_COLORS[memoryType] ?? FALLBACK_COLOR
}

type GraphNode = SimulationNodeDatum & {
  id: string
  kind: 'memory' | 'entity' | 'anchor'
  label: string
  memoryType?: string
  memoryId?: string
}

type GraphLink = SimulationLinkDatum<GraphNode> & {
  rel: string
  /** Invisible session-cluster edge for memories with no entity links. */
  anchor?: boolean
}

const memoryNodeId = (memoryId: string): string => `mem:${memoryId}`
const entityNodeId = (entityId: string): string => `ent:${entityId}`
const anchorNodeId = (sessionId: string): string => `ses:${sessionId}`

function buildGraph(graph: MemoryGraphData): { nodes: GraphNode[]; links: GraphLink[] } {
  const nodes = new Map<string, GraphNode>()
  const links: GraphLink[] = []
  const mentioned = new Set<string>()

  for (const node of graph.nodes) {
    nodes.set(memoryNodeId(node.memory_id), {
      id: memoryNodeId(node.memory_id),
      kind: 'memory',
      label: node.preview,
      memoryType: node.memory_type,
      memoryId: node.memory_id,
    })
  }
  for (const entity of graph.entities) {
    nodes.set(entityNodeId(entity.entity_id), {
      id: entityNodeId(entity.entity_id),
      kind: 'entity',
      label: entity.name,
    })
  }
  // Session anchors cluster orphans; EXTRACTED_FROM points at an opaque source
  // hash rather than a session id, so it is skipped entirely.
  for (const session of graph.sessions) {
    nodes.set(anchorNodeId(session.session_id), {
      id: anchorNodeId(session.session_id),
      kind: 'anchor',
      label: session.title,
    })
  }
  for (const edge of graph.edges) {
    if (edge.rel === 'MENTIONS' && edge.dst_label === 'Entity') {
      const src = memoryNodeId(edge.src)
      const dst = entityNodeId(edge.dst)
      if (nodes.has(src) && nodes.has(dst)) {
        links.push({ source: src, target: dst, rel: edge.rel })
        mentioned.add(src)
      }
      continue
    }
    if (edge.rel === 'RELATES_TO' || edge.rel === 'DERIVED_FROM') {
      const src = memoryNodeId(edge.src)
      const dst = memoryNodeId(edge.dst)
      if (nodes.has(src) && nodes.has(dst)) {
        links.push({ source: src, target: dst, rel: edge.rel })
      }
    }
  }
  for (const node of graph.nodes) {
    if (node.session_id === null) continue
    const anchor = anchorNodeId(node.session_id)
    const memory = memoryNodeId(node.memory_id)
    if (!nodes.has(anchor) || mentioned.has(memory)) continue
    links.push({ source: memory, target: anchor, rel: 'SESSION', anchor: true })
  }
  return { nodes: [...nodes.values()], links }
}

/** Node labels stay glanceable — previews/names are capped for density. */
const nodeLabel = (node: GraphNode): string => {
  const limit = node.kind === 'entity' ? 16 : 12
  const text = node.label.replace(/\s+/g, ' ').trim()
  return text.length > limit ? `${text.slice(0, limit)}…` : text
}

type Props = {
  graph: MemoryGraphData
  onSelect: (memoryId: string) => void
}

export function MemoryGraph({ graph, onSelect }: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const groupRef = useRef<SVGGElement>(null)
  const lineRefs = useRef<(SVGLineElement | null)[]>([])
  const edgeLabelRefs = useRef<(SVGTextElement | null)[]>([])
  const nodeRefs = useRef<(SVGGElement | null)[]>([])
  const draggedRef = useRef(false)
  const { nodes, links } = useMemo(() => buildGraph(graph), [graph])
  // DOM order must match this array 1:1 — anchors simulate invisibly but
  // are skipped by the renderer, so they cannot join the d3 data binding.
  const renderedNodes = useMemo(() => nodes.filter(node => node.kind !== 'anchor'), [nodes])

  useEffect(() => {
    const svg = svgRef.current
    const group = groupRef.current
    if (svg === null || group === null) return undefined

    const simulation = forceSimulation(nodes)
      .force(
        'link',
        forceLink<GraphNode, GraphLink>(links)
          .id(node => node.id)
          .distance(link => (link.anchor === true ? 90 : 55))
          .strength(link => (link.anchor === true ? 0.3 : 0.7)),
      )
      .force('charge', forceManyBody().strength(-140))
      .force('collide', forceCollide<GraphNode>().radius(node => (node.kind === 'memory' ? 16 : 10)))
      .force('x', forceX(0).strength(0.06))
      .force('y', forceY(0).strength(0.06))

    simulation.on('tick', () => {
      links.forEach((link, index) => {
        const line = lineRefs.current[index]
        const label = edgeLabelRefs.current[index]
        const source = link.source as GraphNode
        const target = link.target as GraphNode
        if (source.x === undefined || target.x === undefined
          || source.y === undefined || target.y === undefined) return
        if (line != null) {
          line.setAttribute('x1', String(source.x))
          line.setAttribute('y1', String(source.y))
          line.setAttribute('x2', String(target.x))
          line.setAttribute('y2', String(target.y))
        }
        if (label != null) {
          label.setAttribute('x', String((source.x + target.x) / 2))
          label.setAttribute('y', String((source.y + target.y) / 2))
        }
      })
      renderedNodes.forEach((node, index) => {
        const element = nodeRefs.current[index]
        if (element == null || node.x === undefined || node.y === undefined) return
        element.setAttribute('transform', `translate(${node.x},${node.y})`)
      })
    })

    const zoomBehavior: ZoomBehavior<SVGSVGElement, unknown> = zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 3])
      .on('zoom', (event: D3ZoomEvent<SVGSVGElement, unknown>) => {
        select(group).attr('transform', String(event.transform))
      })
    const svgSelection = select(svg)
    svgSelection.call(zoomBehavior)
    const bounds = svg.getBoundingClientRect()
    // Seed the zoom state directly: zoom.transform() needs svg.width.baseVal,
    // which jsdom does not implement — setting __zoom keeps state consistent
    // without touching the extent.
    const initialTransform = zoomIdentity
      .translate(bounds.width / 2, bounds.height / 2)
      .scale(0.9)
    svgSelection.property('__zoom', initialTransform)
    select(group).attr('transform', String(initialTransform))

    const dragBehavior = drag<SVGGElement, GraphNode>()
      .on('start', (event: D3DragEvent<SVGGElement, GraphNode, GraphNode>) => {
        draggedRef.current = false
        if (event.active === 0) simulation.alphaTarget(0.15).restart()
        event.subject.fx = event.subject.x
        event.subject.fy = event.subject.y
      })
      .on('drag', (event: D3DragEvent<SVGGElement, GraphNode, GraphNode>) => {
        draggedRef.current = true
        event.subject.fx = event.x
        event.subject.fy = event.y
      })
      .on('end', (event: D3DragEvent<SVGGElement, GraphNode, GraphNode>) => {
        if (event.active === 0) simulation.alphaTarget(0)
        event.subject.fx = null
        event.subject.fy = null
      })
    select(svg)
      .selectAll<SVGGElement, GraphNode>('g[data-node]')
      .data(renderedNodes)
      .call(dragBehavior)

    return () => {
      simulation.stop()
      svgSelection.on('.zoom', null)
    }
  }, [nodes, links, renderedNodes])

  return (
    <svg ref={svgRef} className={styles.canvas} role="img" aria-label="记忆知识图谱">
      <g ref={groupRef}>
        {links.map((link, index) => (
          link.anchor === true ? null : (
            <g key={`${link.rel}:${index}`}>
              <line
                ref={element => { lineRefs.current[index] = element }}
                className={styles.edge}
                data-rel={link.rel}
              />
              <text
                ref={element => { edgeLabelRefs.current[index] = element }}
                className={styles.edgeLabel}
              >
                {link.rel}
              </text>
            </g>
          )
        ))}
        {renderedNodes.map((node, index) => (
          <g
            key={node.id}
            ref={element => { nodeRefs.current[index] = element }}
            data-node={node.id}
            className={styles.node}
            onClick={() => {
              if (draggedRef.current) return
              if (node.kind === 'memory' && node.memoryId !== undefined) onSelect(node.memoryId)
            }}
          >
            <title>{node.label}</title>
            {node.kind === 'memory'
              ? <circle r={9} fill={typeColor(node.memoryType ?? '')} />
              : <circle r={4.5} fill="var(--muted-2)" />}
            <text className={styles.nodeLabel} y={node.kind === 'memory' ? 18 : 12}>
              {nodeLabel(node)}
            </text>
          </g>
        ))}
      </g>
    </svg>
  )
}
