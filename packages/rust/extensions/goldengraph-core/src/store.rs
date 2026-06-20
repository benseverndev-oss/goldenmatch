//! SP2 — native portable store + bi-temporal model.
//!
//! Durable, portable persistence baked into the pyo3-free core (WASM/C inherit
//! it in SP5). Identity is keyed on a host-supplied opaque `record_key` (the
//! suite's `:h1:` fingerprint — the core never computes it). Appends reconcile
//! against stored identity by record-key overlap via the **plurality-heir**
//! rule (one rule → total + collision-free). Edges are append-only bi-temporal
//! versions; `as_of(valid_t, tx_t)` filters on both the valid and transaction
//! axes. Snapshots are canonical JSON (`serde_json`), parity-diffable.
//!
//! Scope line: identity (which id) and edge facts are bi-temporal; entity
//! *attributes* (canonical_name / surface_names) reflect the latest state, not
//! their value as-of `tx_t` (attribute history is out of SP2).

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};

use crate::model::{Edge, EntityNode, Graph};

/// Durable entity id, owned by the store. Distinct from SP1's within-build
/// `EntityId` (`u32`). Assigned once, monotonic, never reused.
pub type StableId = u64;

/// Store load error.
#[derive(Clone, Debug, PartialEq)]
pub enum StoreError {
    Parse(String),
}

// ---- append input (SP4 builds this from extraction + SP1 resolution) -------

/// A resolved entity in an incoming batch, anchored by its members' record keys.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BatchEntity {
    pub local_id: u32,
    pub canonical_name: String,
    pub typ: String,
    pub surface_names: Vec<String>,
    /// Host-supplied stable keys (e.g. `:h1:` fingerprints). Need not be sorted.
    pub record_keys: Vec<String>,
}

/// A relationship in an incoming batch (endpoints are batch-local ids).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BatchEdge {
    pub subj_local: u32,
    pub predicate: String,
    pub obj_local: u32,
    pub valid_from: i64,
    pub valid_to: Option<i64>,
    pub source_refs: Vec<String>,
}

/// One incremental append: a resolved batch plus its transaction time.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StoreBatch {
    pub entities: Vec<BatchEntity>,
    pub edges: Vec<BatchEdge>,
    pub ingested_at: i64,
}

// ---- stored state ----------------------------------------------------------

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StoredEntity {
    pub id: StableId,
    pub canonical_name: String,
    pub typ: String,
    pub surface_names: Vec<String>,
    pub record_keys: Vec<String>,
    pub created_at: i64,
    pub superseded_by: Option<StableId>,
    pub superseded_at: Option<i64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StoredEdge {
    pub subj: StableId,
    pub predicate: String,
    pub obj: StableId,
    pub valid_from: i64,
    pub valid_to: Option<i64>,
    pub ingested_at: i64,
    pub source_refs: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub enum HistoryEvent {
    Merge {
        kept: StableId,
        absorbed: Vec<StableId>,
        at: i64,
    },
    Split {
        from: StableId,
        into: Vec<StableId>,
        at: i64,
    },
}

/// The portable bi-temporal store.
#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct GraphStore {
    pub entities: BTreeMap<StableId, StoredEntity>,
    pub edges: Vec<StoredEdge>,
    pub history: Vec<HistoryEvent>,
    pub next_id: StableId,
}

/// Canonical name = longest surface form; tie → lexically smallest.
fn canonical_of(surface_names: &[String]) -> String {
    surface_names
        .iter()
        .cloned()
        .reduce(|best, s| {
            if s.len() > best.len() || (s.len() == best.len() && s < best) {
                s
            } else {
                best
            }
        })
        .unwrap_or_default()
}

/// `None` (open-ended) sorts AFTER any concrete `valid_to`.
fn cmp_valid_to(a: &Option<i64>, b: &Option<i64>) -> Ordering {
    match (a, b) {
        (None, None) => Ordering::Equal,
        (None, Some(_)) => Ordering::Greater,
        (Some(_), None) => Ordering::Less,
        (Some(x), Some(y)) => x.cmp(y),
    }
}

impl GraphStore {
    /// Open from a canonical snapshot (`None` → empty store).
    pub fn open(snapshot: Option<&str>) -> Result<Self, StoreError> {
        match snapshot {
            None => Ok(Self::default()),
            Some(s) => serde_json::from_str(s).map_err(|e| StoreError::Parse(e.to_string())),
        }
    }

    /// Is this entity current (not superseded) as-of transaction time `t`?
    fn current_at(e: &StoredEntity, t: i64) -> bool {
        e.superseded_at.is_none_or(|sa| sa > t)
    }

    /// Resolve `id` to the entity current as-of transaction time `tx_t`,
    /// following the supersession chain only through hops recorded at/before
    /// `tx_t`. Bounded by entity count (supersession is acyclic + monotonic).
    fn current_id(&self, mut id: StableId, tx_t: i64) -> StableId {
        for _ in 0..=self.entities.len() {
            match self.entities.get(&id) {
                Some(e) => match (e.superseded_by, e.superseded_at) {
                    (Some(next), Some(sa)) if sa <= tx_t => id = next,
                    _ => return id,
                },
                None => return id,
            }
        }
        id
    }

    /// Reconcile an incoming batch against stored identity and append its facts.
    /// Deterministic and independent of within-batch entity/edge order.
    pub fn append(&mut self, batch: StoreBatch) {
        let at = batch.ingested_at;
        let n = batch.entities.len();

        // sorted, deduped record keys per batch entity (tie-break + storage)
        let sorted_keys: Vec<Vec<String>> = batch
            .entities
            .iter()
            .map(|be| {
                let mut k = be.record_keys.clone();
                k.sort();
                k.dedup();
                k
            })
            .collect();

        // record_key -> stored id, over entities current as-of `at` (a key
        // belongs to exactly one current entity, so this is well-defined).
        let mut key_to_stored: BTreeMap<&str, StableId> = BTreeMap::new();
        for (&id, e) in &self.entities {
            if Self::current_at(e, at) {
                for k in &e.record_keys {
                    key_to_stored.insert(k.as_str(), id);
                }
            }
        }

        // overlap counts: batch entity i -> (stored id -> shared-key count)
        let mut overlaps: Vec<BTreeMap<StableId, usize>> = vec![BTreeMap::new(); n];
        for (i, keys) in sorted_keys.iter().enumerate() {
            for k in keys {
                if let Some(&sid) = key_to_stored.get(k.as_str()) {
                    *overlaps[i].entry(sid).or_insert(0) += 1;
                }
            }
        }

        // every stored id touched this batch
        let mut all_stored: BTreeSet<StableId> = BTreeSet::new();
        for ov in &overlaps {
            for &sid in ov.keys() {
                all_stored.insert(sid);
            }
        }

        // heir of each stored id: max overlap; tie -> lex-smallest sorted_keys
        let mut heir: BTreeMap<StableId, usize> = BTreeMap::new();
        for &sid in &all_stored {
            let mut best: Option<usize> = None;
            for (i, ov) in overlaps.iter().enumerate() {
                if let Some(&cnt) = ov.get(&sid) {
                    best = Some(match best {
                        None => i,
                        Some(b) => {
                            let bc = overlaps[b][&sid];
                            if cnt > bc || (cnt == bc && sorted_keys[i] < sorted_keys[b]) {
                                i
                            } else {
                                b
                            }
                        }
                    });
                }
            }
            heir.insert(sid, best.expect("stored id in all_stored has ≥1 overlap"));
        }

        // inherited(i) = stored ids whose heir is i (sorted: heir iterated by sid)
        let mut inherited: Vec<Vec<StableId>> = vec![Vec::new(); n];
        for (&sid, &i) in &heir {
            inherited[i].push(sid);
        }

        // assign ids: inheritance first (content-determined), then mint in
        // sorted_keys order so minting is independent of batch position.
        let mut assigned: Vec<Option<StableId>> = vec![None; n];
        for i in 0..n {
            match inherited[i].as_slice() {
                [] => {}
                [single] => assigned[i] = Some(*single),
                many => assigned[i] = Some(*many.iter().min().unwrap()),
            }
        }
        let mut minters: Vec<usize> = (0..n).filter(|&i| assigned[i].is_none()).collect();
        minters.sort_by(|&a, &b| sorted_keys[a].cmp(&sorted_keys[b]));
        for i in minters {
            assigned[i] = Some(self.next_id);
            self.next_id += 1;
        }
        let assigned: Vec<StableId> = assigned.into_iter().map(|x| x.unwrap()).collect();

        // merges: emit in sorted_keys order for canonical history
        let mut order: Vec<usize> = (0..n).collect();
        order.sort_by(|&a, &b| sorted_keys[a].cmp(&sorted_keys[b]));
        for &i in &order {
            if inherited[i].len() > 1 {
                let kept = assigned[i];
                let absorbed: Vec<StableId> = inherited[i]
                    .iter()
                    .copied()
                    .filter(|&x| x != kept)
                    .collect();
                for &a in &absorbed {
                    if let Some(e) = self.entities.get_mut(&a) {
                        e.superseded_by = Some(kept);
                        e.superseded_at = Some(at);
                    }
                }
                self.history
                    .push(HistoryEvent::Merge { kept, absorbed, at });
            }
        }

        // splits: a stored id whose keys landed across >1 batch entity
        for &sid in &all_stored {
            let absorbers: Vec<usize> =
                (0..n).filter(|&i| overlaps[i].contains_key(&sid)).collect();
            if absorbers.len() > 1 {
                let mut into: Vec<StableId> = absorbers.iter().map(|&i| assigned[i]).collect();
                into.sort();
                into.dedup();
                self.history.push(HistoryEvent::Split {
                    from: sid,
                    into,
                    at,
                });
            }
        }

        // upsert entities (BTreeMap → insert order irrelevant to final state)
        for (i, be) in batch.entities.iter().enumerate() {
            let id = assigned[i];
            // The batch entity's keys/surfaces are AUTHORITATIVE for this entity
            // (each batch is a full resolution pass), so keys it no longer states
            // genuinely leave it — that is what makes a split drop the split-off
            // keys instead of re-absorbing them. Prior survivor keys are NOT
            // unioned back in; only absorbed (merged) entities' keys fold in.
            let mut keys = sorted_keys[i].clone();
            let mut surfaces = be.surface_names.clone();
            let (created_at, typ) = match self.entities.get(&id) {
                Some(prev) => (prev.created_at, prev.typ.clone()),
                None => (at, be.typ.clone()),
            };
            // fold in absorbed entities' keys/surfaces
            for &sid in &inherited[i] {
                if sid != id {
                    if let Some(ab) = self.entities.get(&sid) {
                        keys.extend(ab.record_keys.iter().cloned());
                        surfaces.extend(ab.surface_names.iter().cloned());
                    }
                }
            }
            keys.sort();
            keys.dedup();
            surfaces.sort();
            surfaces.dedup();
            let canonical_name = canonical_of(&surfaces);
            self.entities.insert(
                id,
                StoredEntity {
                    id,
                    canonical_name,
                    typ,
                    surface_names: surfaces,
                    record_keys: keys,
                    created_at,
                    superseded_by: None,
                    superseded_at: None,
                },
            );
        }

        // edges: remap local → assigned StableId (absorbed → kept, since
        // `assigned` already holds the kept id), append-only versioned.
        let mut local_to_stable: BTreeMap<u32, StableId> = BTreeMap::new();
        for (i, be) in batch.entities.iter().enumerate() {
            local_to_stable.insert(be.local_id, assigned[i]);
        }
        for e in &batch.edges {
            let subj = local_to_stable[&e.subj_local];
            let obj = local_to_stable[&e.obj_local];
            let mut refs = e.source_refs.clone();
            refs.sort();
            refs.dedup();
            self.edges.push(StoredEdge {
                subj,
                predicate: e.predicate.clone(),
                obj,
                valid_from: e.valid_from,
                valid_to: e.valid_to,
                ingested_at: at,
                source_refs: refs,
            });
        }
    }

    /// Bi-temporal slice: the graph the store believed as-of transaction time
    /// `tx_t`, restricted to facts valid at `valid_t`. Returns an SP1 `Graph`
    /// (view-local `EntityId`s assigned in ascending `StableId` order), so
    /// `neighborhood` / `seeds_by_name` run over it unchanged.
    pub fn as_of(&self, valid_t: i64, tx_t: i64) -> Graph {
        // latest version per (subj, predicate, obj) known as-of tx_t
        let mut latest: BTreeMap<(StableId, &str, StableId), &StoredEdge> = BTreeMap::new();
        for e in &self.edges {
            if e.ingested_at > tx_t {
                continue;
            }
            let key = (e.subj, e.predicate.as_str(), e.obj);
            let better = match latest.get(&key) {
                None => true,
                Some(prev) => {
                    (
                        e.ingested_at,
                        e.valid_from,
                        cmp_valid_to(&e.valid_to, &prev.valid_to),
                    ) > (prev.ingested_at, prev.valid_from, Ordering::Equal)
                }
            };
            if better {
                latest.insert(key, e);
            }
        }

        // valid-window filter + endpoint resolution as-of tx_t, deduped
        let mut acc: BTreeMap<(u32, String, u32), BTreeSet<String>> = BTreeMap::new();
        let mut sids: BTreeSet<StableId> = BTreeSet::new();
        let mut resolved: Vec<(StableId, &str, StableId, &Vec<String>)> = Vec::new();
        for e in latest.values() {
            let within = e.valid_from <= valid_t && e.valid_to.is_none_or(|vt| valid_t < vt);
            if !within {
                continue;
            }
            let s = self.current_id(e.subj, tx_t);
            let o = self.current_id(e.obj, tx_t);
            sids.insert(s);
            sids.insert(o);
            resolved.push((s, e.predicate.as_str(), o, &e.source_refs));
        }

        // view-local EntityId per StableId, ascending
        let id_list: Vec<StableId> = sids.into_iter().collect();
        let sid_to_eid: BTreeMap<StableId, u32> = id_list
            .iter()
            .enumerate()
            .map(|(idx, &sid)| (sid, idx as u32))
            .collect();

        let entities: Vec<EntityNode> = id_list
            .iter()
            .map(|&sid| {
                let se = &self.entities[&sid];
                EntityNode {
                    entity_id: sid_to_eid[&sid],
                    canonical_name: se.canonical_name.clone(),
                    typ: se.typ.clone(),
                    members: Vec::new(),
                    surface_names: se.surface_names.clone(),
                }
            })
            .collect();

        for (s, p, o, refs) in resolved {
            let entry = acc
                .entry((sid_to_eid[&s], p.to_string(), sid_to_eid[&o]))
                .or_default();
            for r in refs {
                entry.insert(r.clone());
            }
        }
        let edges: Vec<Edge> = acc
            .into_iter()
            .map(|((subj, predicate, obj), refs)| Edge {
                subj,
                predicate,
                obj,
                source_refs: refs.into_iter().collect(),
            })
            .collect();

        Graph { entities, edges }
    }

    /// Canonical JSON snapshot: entities by id (`BTreeMap`), edges sorted by the
    /// full tuple, history in event order. Round-trip byte-identical.
    pub fn snapshot(&self) -> String {
        let mut edges = self.edges.clone();
        edges.sort_by(|a, b| {
            a.subj
                .cmp(&b.subj)
                .then_with(|| a.predicate.cmp(&b.predicate))
                .then_with(|| a.obj.cmp(&b.obj))
                .then_with(|| a.valid_from.cmp(&b.valid_from))
                .then_with(|| cmp_valid_to(&a.valid_to, &b.valid_to))
                .then_with(|| a.ingested_at.cmp(&b.ingested_at))
                .then_with(|| a.source_refs.cmp(&b.source_refs))
        });
        let canonical = GraphStore {
            entities: self.entities.clone(),
            edges,
            history: self.history.clone(),
            next_id: self.next_id,
        };
        serde_json::to_string(&canonical).expect("GraphStore is serializable")
    }

    /// History events literally naming `id` (no chain-follow across supersession).
    pub fn history(&self, id: StableId) -> Vec<HistoryEvent> {
        self.history
            .iter()
            .filter(|ev| match ev {
                HistoryEvent::Merge { kept, absorbed, .. } => *kept == id || absorbed.contains(&id),
                HistoryEvent::Split { from, into, .. } => *from == id || into.contains(&id),
            })
            .cloned()
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn be(local: u32, name: &str, keys: &[&str]) -> BatchEntity {
        BatchEntity {
            local_id: local,
            canonical_name: name.into(),
            typ: "t".into(),
            surface_names: vec![name.into()],
            record_keys: keys.iter().map(|s| s.to_string()).collect(),
        }
    }
    fn edge(s: u32, o: u32, vf: i64, vt: Option<i64>, refs: &[&str]) -> BatchEdge {
        BatchEdge {
            subj_local: s,
            predicate: "r".into(),
            obj_local: o,
            valid_from: vf,
            valid_to: vt,
            source_refs: refs.iter().map(|x| x.to_string()).collect(),
        }
    }
    fn batch(entities: Vec<BatchEntity>, edges: Vec<BatchEdge>, at: i64) -> StoreBatch {
        StoreBatch {
            entities,
            edges,
            ingested_at: at,
        }
    }

    // ---- Task 1 ----
    #[test]
    fn empty_store_is_empty() {
        let s = GraphStore::open(None).unwrap();
        assert!(s.entities.is_empty() && s.edges.is_empty() && s.history.is_empty());
        assert_eq!(s.next_id, 0);
    }

    // ---- Task 2 ----
    #[test]
    fn append_new_mints_id() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(vec![be(0, "X", &["k1"])], vec![], 10));
        assert_eq!(s.entities.len(), 1);
        assert!(s.entities.contains_key(&0));
        assert_eq!(s.next_id, 1);
    }

    #[test]
    fn append_unchanged_keeps_id() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(vec![be(0, "X", &["k1"])], vec![], 10));
        s.append(batch(vec![be(0, "X", &["k1"])], vec![], 20));
        assert_eq!(s.entities.len(), 1);
        assert!(s.entities.contains_key(&0));
    }

    #[test]
    fn append_merge_two_into_one() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(
            vec![be(0, "A", &["a"]), be(1, "B", &["b"])],
            vec![],
            10,
        ));
        assert_eq!(s.entities.len(), 2);
        // a single entity carrying both keys -> heir of both -> merge into id 0
        s.append(batch(vec![be(0, "AB", &["a", "b"])], vec![], 20));
        let merges: Vec<_> = s
            .history
            .iter()
            .filter(|e| matches!(e, HistoryEvent::Merge { .. }))
            .collect();
        assert_eq!(merges.len(), 1);
        assert_eq!(
            merges[0],
            &HistoryEvent::Merge {
                kept: 0,
                absorbed: vec![1],
                at: 20
            }
        );
        assert_eq!(s.entities[&1].superseded_by, Some(0));
        assert!(s.entities[&0].record_keys == vec!["a".to_string(), "b".to_string()]);
    }

    #[test]
    fn append_split_keeps_id_with_plurality_heir() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(vec![be(0, "S", &["a", "b", "c"])], vec![], 10));
        // a={local0}, bc={local1}: heir of 0 = bc (overlap 2>1) -> keeps 0, a mints 1
        s.append(batch(
            vec![be(0, "Sa", &["a"]), be(1, "Sbc", &["b", "c"])],
            vec![],
            20,
        ));
        let splits: Vec<_> = s
            .history
            .iter()
            .filter(|e| matches!(e, HistoryEvent::Split { .. }))
            .collect();
        assert_eq!(splits.len(), 1);
        assert_eq!(
            splits[0],
            &HistoryEvent::Split {
                from: 0,
                into: vec![0, 1],
                at: 20
            }
        );
        // the bc entity (plurality heir) kept id 0
        assert!(s.entities[&0].record_keys.contains(&"b".to_string()));
    }

    #[test]
    fn append_double_claim_no_collision() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(vec![be(0, "S", &["a", "b", "c", "d"])], vec![], 10));
        // n1={a,b} n2={c,d}: tie (2 vs 2) -> heir = lex-smallest keys = n1 ("a,b")
        s.append(batch(
            vec![be(0, "n1", &["a", "b"]), be(1, "n2", &["c", "d"])],
            vec![],
            20,
        ));
        // no two entities share an id, n1 kept 0, n2 minted 1
        assert!(s.entities.contains_key(&0) && s.entities.contains_key(&1));
        assert_eq!(
            s.entities[&0].record_keys,
            vec!["a".to_string(), "b".to_string()]
        );
        assert_eq!(
            s.entities[&1].record_keys,
            vec!["c".to_string(), "d".to_string()]
        );
    }

    // ---- Task 3 ----
    #[test]
    fn append_stores_edges_remapped_and_versioned() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(
            vec![be(0, "X", &["x"]), be(1, "Y", &["y"])],
            vec![edge(0, 1, 10, None, &["s2", "s1"])],
            100,
        ));
        assert_eq!(s.edges.len(), 1);
        let e = &s.edges[0];
        assert_eq!((e.subj, e.obj, e.ingested_at), (0, 1, 100));
        assert_eq!(e.source_refs, vec!["s1".to_string(), "s2".to_string()]);
    }

    // ---- Task 4 ----
    #[test]
    fn as_of_valid_axis() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(
            vec![be(0, "A", &["a"]), be(1, "B", &["b"])],
            vec![edge(0, 1, 10, Some(20), &["s"])],
            100,
        ));
        assert_eq!(s.as_of(15, 1000).edges.len(), 1); // within [10,20)
        assert_eq!(s.as_of(25, 1000).edges.len(), 0); // after valid_to
    }

    #[test]
    fn as_of_tx_axis_correction() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(
            vec![be(0, "X", &["x"]), be(1, "Y", &["y"])],
            vec![edge(0, 1, 10, None, &["s"])],
            100,
        ));
        // correction: same triple, now ends at 20, learned at tx 200
        s.append(batch(
            vec![be(0, "X", &["x"]), be(1, "Y", &["y"])],
            vec![edge(0, 1, 10, Some(20), &["s"])],
            200,
        ));
        assert_eq!(s.as_of(25, 150).edges.len(), 1); // only the open version known
        assert_eq!(s.as_of(25, 250).edges.len(), 0); // correction known: window ends at 20
    }

    #[test]
    fn as_of_supersession_chain() {
        let mut s = GraphStore::open(None).unwrap();
        // A(0), B(1), C(2); edges A->C and B->C
        s.append(batch(
            vec![be(0, "A", &["a"]), be(1, "B", &["b"]), be(2, "C", &["c"])],
            vec![edge(0, 2, 0, None, &["e1"]), edge(1, 2, 0, None, &["e2"])],
            100,
        ));
        // later A and B resolve together (one entity w/ keys a,b) -> merge 1 into 0 at 200
        s.append(batch(
            vec![be(0, "AB", &["a", "b"]), be(1, "C", &["c"])],
            vec![],
            200,
        ));
        // before the merge: A,B,C distinct -> 3 entities, 2 edges
        let before = s.as_of(50, 150);
        assert_eq!(before.entities.len(), 3);
        assert_eq!(before.edges.len(), 2);
        // after the merge: B->C remaps to A->C, collapses with A->C -> 2 entities, 1 edge
        let after = s.as_of(50, 250);
        assert_eq!(after.entities.len(), 2);
        assert_eq!(after.edges.len(), 1);
    }

    #[test]
    fn current_id_follows_two_hop_chain_as_of_tx() {
        let mut s = GraphStore::open(None).unwrap();
        let mk = |id: StableId, by: Option<StableId>, at: Option<i64>| StoredEntity {
            id,
            canonical_name: format!("e{id}"),
            typ: "t".into(),
            surface_names: vec![format!("e{id}")],
            record_keys: vec![],
            created_at: 0,
            superseded_by: by,
            superseded_at: at,
        };
        s.entities.insert(0, mk(0, None, None));
        s.entities.insert(1, mk(1, Some(0), Some(200)));
        s.entities.insert(2, mk(2, Some(1), Some(100)));
        assert_eq!(s.current_id(2, 50), 2); // no hop yet
        assert_eq!(s.current_id(2, 150), 1); // first hop only (at 100)
        assert_eq!(s.current_id(2, 250), 0); // both hops
    }

    // ---- Task 5 ----
    #[test]
    fn snapshot_round_trip_byte_identical() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(
            vec![be(0, "A", &["a"]), be(1, "B", &["b"])],
            vec![
                edge(0, 1, 10, Some(30), &["s1"]),
                edge(1, 0, 5, None, &["s2"]),
            ],
            100,
        ));
        let s1 = s.snapshot();
        let reopened = GraphStore::open(Some(&s1)).unwrap();
        assert_eq!(s1, reopened.snapshot());
    }

    #[test]
    fn snapshot_within_batch_order_independent() {
        let entities_a = vec![be(0, "A", &["a"]), be(1, "B", &["b"]), be(2, "C", &["c"])];
        let mut entities_b = entities_a.clone();
        entities_b.reverse(); // permute within-batch order
        let edges_a = vec![edge(0, 1, 0, None, &["x"]), edge(1, 2, 0, None, &["y"])];
        let mut edges_b = edges_a.clone();
        edges_b.reverse();

        let mut sa = GraphStore::open(None).unwrap();
        sa.append(batch(entities_a, edges_a, 100));
        let mut sb = GraphStore::open(None).unwrap();
        sb.append(batch(entities_b, edges_b, 100));
        assert_eq!(sa.snapshot(), sb.snapshot());
    }

    // ---- Task 6 ----
    #[test]
    fn history_names_both_sides_of_merge() {
        let mut s = GraphStore::open(None).unwrap();
        s.append(batch(
            vec![be(0, "A", &["a"]), be(1, "B", &["b"])],
            vec![],
            10,
        ));
        s.append(batch(vec![be(0, "AB", &["a", "b"])], vec![], 20));
        assert_eq!(s.history(0).len(), 1); // kept
        assert_eq!(s.history(1).len(), 1); // absorbed
        assert!(s.history(2).is_empty());
    }
}
