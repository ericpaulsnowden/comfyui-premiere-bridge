/**
 * @file Flat list of every node in a graph INCLUDING nodes inside ComfyUI
 * subgraphs. Kept import-free so it can be unit-tested under plain Node
 * (tests/test_graph_nodes_js.py).
 *
 * A subgraph instance is an ordinary node on its parent graph carrying a
 * `subgraph` object that has its own `_nodes`; iterating only the root
 * graph's `_nodes` misses everything inside it. Same walk as the sibling
 * pack's live.js `isArmed`.
 */

/** Both litegraph spellings of a graph's node list. */
function ownNodes(graph) {
  return graph?._nodes ?? graph?.nodes ?? []
}

/**
 * @param {object} graph - `app.graph` or a subgraph.
 * @returns {object[]} every node, parents before their children.
 */
export function collectGraphNodes(graph) {
  const out = []
  const seen = new Set() // a subgraph definition may be instanced more than once
  const walk = (g) => {
    if (!g || seen.has(g)) return
    seen.add(g)
    for (const node of ownNodes(g)) {
      out.push(node)
      if (node?.subgraph) walk(node.subgraph)
    }
  }
  walk(graph)
  return out
}
