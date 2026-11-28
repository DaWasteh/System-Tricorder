/-!
# Formal Specification & Proofs for System Tricorder Tile Layout & Migration

Models the pure layout functions in `system_tricorder.py` (lines 4568–4791):
- `_tile_rows` / `_flatten_tile_rows`
- `_move_tile_to_new_row`
- `_layout_anchor`
- `_merge_layout_tiles`
- `_preserve_dormant_layout`

All theorems below are proved without `sorry` and using only Lean 4 standard axioms.
-/

namespace Tricorder.Layout

private theorem length_dropWhile_le (p : α → Bool) (xs : List α) :
    (xs.dropWhile p).length ≤ xs.length := by
  induction xs with
  | nil => simp
  | cons x xs ih =>
    simp only [List.dropWhile_cons]
    split
    · simp; omega
    · simp

private theorem mem_takeWhile_imp_true {p : α → Bool} {xs : List α} {x : α}
    (h : x ∈ xs.takeWhile p) : p x = true := by
  induction xs with
  | nil => simp at h
  | cons y ys ih =>
    simp only [List.takeWhile_cons] at h
    split at h
    · rename_i hpy
      simp only [List.mem_cons] at h
      rcases h with rfl | h'
      · exact hpy
      · exact ih h'
    · simp at h

private theorem nodup_reverse_of_nodup {xs : List α} (h : xs.Nodup) : xs.reverse.Nodup := by
  induction xs with
  | nil => simp
  | cons x xs ih =>
    rw [List.nodup_cons] at h
    rw [List.reverse_cons, List.nodup_append]
    refine ⟨ih h.2, by simp, ?_⟩
    intro a ha b hb heq
    simp at hb; subst hb; subst heq
    exact h.1 (List.mem_reverse.mp ha)

def rowSentinel : String := "__row__"

def visibleTiles (order : List String) : List String :=
  order.filter (fun s => s != rowSentinel)

/-- Canonical row-break normal form: no leading, trailing, or consecutive `"__row__"`. -/
def isCanonicalAux (lastWasBreak : Bool) : List String → Bool
  | [] => !lastWasBreak
  | [x] => x != rowSentinel
  | x :: rest =>
    if x == rowSentinel then
      if lastWasBreak then false else isCanonicalAux true rest
    else
      isCanonicalAux false rest

def isCanonicalOrder : List String → Bool
  | [] => true
  | xs => isCanonicalAux true xs

def CanonicalOrder (xs : List String) : Prop :=
  isCanonicalOrder xs = true

-- ── 1. Row parsing & flattening ───────────────────────────────────────────────

def tileRowsAux (curRev : List String) : List String → List (List String)
  | [] => if curRev.isEmpty then [] else [curRev.reverse]
  | x :: xs =>
    if x == rowSentinel then
      if curRev.isEmpty then tileRowsAux [] xs
      else curRev.reverse :: tileRowsAux [] xs
    else
      tileRowsAux (x :: curRev) xs

def tileRows (order : List String) : List (List String) :=
  tileRowsAux [] order

def flattenNonEmpty : List (List String) → List String
  | [] => []
  | [r] => r
  | r :: rs => r ++ [rowSentinel] ++ flattenNonEmpty rs

def flattenTileRows (rows : List (List String)) : List String :=
  flattenNonEmpty (rows.filter (fun r => !r.isEmpty))

-- ── Helper lemmas for CanonicalOrder & row parsing ────────────────────────────

private theorem isCanonicalAux_no_sentinel_nonempty
    (r : List String) (hne : r ≠ []) (hno : rowSentinel ∉ r) (b : Bool) :
    isCanonicalAux b r = true := by
  induction r generalizing b with
  | nil => contradiction
  | cons x xs ih =>
    have hx : x ≠ rowSentinel := fun h => hno (by simp [h])
    have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
    have hx_bne : (x != rowSentinel) = true := by simp [bne, hx_beq]
    cases xs with
    | nil =>
      simp [isCanonicalAux, hx_bne]
    | cons y ys =>
      have hno' : rowSentinel ∉ y :: ys := fun h => hno (List.mem_cons_of_mem x h)
      simp [isCanonicalAux, hx_beq]
      exact ih (by simp) hno' false

private theorem isCanonicalAux_append_sentinel
    (r : List String) (rest : List String)
    (hne : r ≠ []) (hno : rowSentinel ∉ r)
    (hrest_ne : rest ≠ []) (hrest : isCanonicalAux true rest = true)
    (b : Bool) :
    isCanonicalAux b (r ++ rowSentinel :: rest) = true := by
  induction r generalizing b with
  | nil => contradiction
  | cons x xs ih =>
    have hx : x ≠ rowSentinel := fun h => hno (by simp [h])
    have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
    cases xs with
    | nil =>
      cases rest with
      | nil => contradiction
      | cons z zs =>
        simp [isCanonicalAux, hx_beq]
        exact hrest
    | cons y ys =>
      have hno' : rowSentinel ∉ y :: ys := fun h => hno (List.mem_cons_of_mem x h)
      have hne' : (y :: ys) ≠ [] := by simp
      have hcons : (x :: y :: ys) ++ rowSentinel :: rest =
          x :: ((y :: ys) ++ rowSentinel :: rest) := rfl
      rw [hcons]
      have htail_ne : ((y :: ys) ++ rowSentinel :: rest) ≠ [] := by simp
      cases htail : ((y :: ys) ++ rowSentinel :: rest) with
      | nil => exact absurd htail htail_ne
      | cons t ts =>
        simp [isCanonicalAux, hx_beq]
        rw [← htail]
        exact ih hne' hno' false

private theorem flattenNonEmpty_ne_nil
    (rows : List (List String)) (hne : rows ≠ [])
    (hall : ∀ r ∈ rows, r ≠ []) :
    flattenNonEmpty rows ≠ [] := by
  cases rows with
  | nil => contradiction
  | cons r rs =>
    cases rs with
    | nil =>
      simpa [flattenNonEmpty] using hall r (by simp)
    | cons r2 rs2 =>
      simp [flattenNonEmpty]

private theorem isCanonicalAux_flattenNonEmpty
    (rows : List (List String))
    (hne : rows ≠ [])
    (hall_ne : ∀ r ∈ rows, r ≠ [])
    (hall_no : ∀ r ∈ rows, rowSentinel ∉ r) :
    isCanonicalAux true (flattenNonEmpty rows) = true := by
  induction rows with
  | nil => contradiction
  | cons r rs ih =>
    have hr_ne : r ≠ [] := hall_ne r (by simp)
    have hr_no : rowSentinel ∉ r := hall_no r (by simp)
    cases rs with
    | nil =>
      simpa [flattenNonEmpty] using isCanonicalAux_no_sentinel_nonempty r hr_ne hr_no true
    | cons r2 rs2 =>
      have hrs_ne : (r2 :: rs2) ≠ [] := by simp
      have hall_ne' : ∀ q ∈ (r2 :: rs2), q ≠ [] := fun q hq => hall_ne q (List.mem_cons_of_mem r hq)
      have hall_no' : ∀ q ∈ (r2 :: rs2), rowSentinel ∉ q := fun q hq => hall_no q (List.mem_cons_of_mem r hq)
      have hsub := ih hrs_ne hall_ne' hall_no'
      have hsub_ne := flattenNonEmpty_ne_nil (r2 :: rs2) hrs_ne hall_ne'
      simp only [flattenNonEmpty, List.append_assoc, List.cons_append, List.nil_append]
      exact isCanonicalAux_append_sentinel r (flattenNonEmpty (r2 :: rs2)) hr_ne hr_no hsub_ne hsub true

theorem canonical_flattenTileRows
    (rows : List (List String))
    (hno : ∀ r ∈ rows, rowSentinel ∉ r) :
    CanonicalOrder (flattenTileRows rows) := by
  unfold CanonicalOrder flattenTileRows
  generalize hf : rows.filter (fun r => !r.isEmpty) = filtered
  have hall_ne : ∀ r ∈ filtered, r ≠ [] := by
    intro r hr
    rw [← hf] at hr
    have hmem := (List.mem_filter.mp hr).2
    cases r with
    | nil => simp at hmem
    | cons _ _ => simp
  have hall_no : ∀ r ∈ filtered, rowSentinel ∉ r := by
    intro r hr
    rw [← hf] at hr
    exact hno r (List.mem_filter.mp hr).1
  cases filtered with
  | nil =>
    simp [flattenNonEmpty, isCanonicalOrder]
  | cons r rs =>
    have hne : flattenNonEmpty (r :: rs) ≠ [] :=
      flattenNonEmpty_ne_nil (r :: rs) (by simp) hall_ne
    cases hflat : flattenNonEmpty (r :: rs) with
    | nil => exact absurd hflat hne
    | cons x xs =>
      rw [← hflat]
      have hcan := isCanonicalAux_flattenNonEmpty (r :: rs) (by simp) hall_ne hall_no
      simpa [isCanonicalOrder, hflat] using hcan

private theorem tileRowsAux_no_sentinel
    (curRev order : List String)
    (hcur : rowSentinel ∉ curRev) :
    ∀ r ∈ tileRowsAux curRev order, rowSentinel ∉ r := by
  induction order generalizing curRev with
  | nil =>
    simp only [tileRowsAux]
    split
    · intro r hr; simp at hr
    · intro r hr
      simp at hr
      subst hr
      simpa [List.mem_reverse] using hcur
  | cons x xs ih =>
    simp only [tileRowsAux]
    split
    · split
      · exact ih [] (by simp)
      · intro r hr
        simp at hr
        rcases hr with rfl | hr
        · simpa [List.mem_reverse] using hcur
        · exact ih [] (by simp) r hr
    · rename_i hne
      have hx : x ≠ rowSentinel := by
        intro h; apply hne; simp [h]
      have hcur' : rowSentinel ∉ x :: curRev := by
        intro hmem
        simp at hmem
        rcases hmem with h1 | h2
        · exact hx h1.symm
        · exact hcur h2
      exact ih (x :: curRev) hcur'

theorem tileRows_no_sentinel (order : List String) :
    ∀ r ∈ tileRows order, rowSentinel ∉ r :=
  tileRowsAux_no_sentinel [] order (by simp)

/-- Theorem 1a: `flattenTileRows (tileRows xs)` is always in canonical normal form. -/
theorem canonical_flatten_tileRows (order : List String) :
    CanonicalOrder (flattenTileRows (tileRows order)) :=
  canonical_flattenTileRows (tileRows order) (tileRows_no_sentinel order)

private theorem tileRowsAux_append_no_sentinel
    (r rest curRev : List String) (hno : rowSentinel ∉ r) :
    tileRowsAux curRev (r ++ rest) = tileRowsAux (r.reverse ++ curRev) rest := by
  induction r generalizing curRev with
  | nil => simp
  | cons x xs ih =>
    have hx : x ≠ rowSentinel := fun h => hno (by simp [h])
    have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
    have hno' : rowSentinel ∉ xs := fun h => hno (List.mem_cons_of_mem x h)
    simp [tileRowsAux, hx_beq, ih (x :: curRev) hno', List.append_assoc]

private theorem tileRowsAux_flattenNonEmpty
    (rows : List (List String))
    (hall_ne : ∀ r ∈ rows, r ≠ [])
    (hall_no : ∀ r ∈ rows, rowSentinel ∉ r) :
    tileRowsAux [] (flattenNonEmpty rows) = rows := by
  induction rows with
  | nil => simp [flattenNonEmpty, tileRowsAux]
  | cons r rs ih =>
    have hr_ne : r ≠ [] := hall_ne r (by simp)
    have hr_no : rowSentinel ∉ r := hall_no r (by simp)
    have hr_rev_ne : r.reverse.isEmpty = false := by
      cases r with
      | nil => contradiction
      | cons _ _ => simp
    cases rs with
    | nil =>
      simp only [flattenNonEmpty]
      have h := tileRowsAux_append_no_sentinel r [] [] hr_no
      simp only [List.append_nil] at h
      rw [h]
      simp [tileRowsAux, hr_rev_ne]
    | cons r2 rs2 =>
      have hall_ne' : ∀ q ∈ (r2 :: rs2), q ≠ [] := fun q hq => hall_ne q (List.mem_cons_of_mem r hq)
      have hall_no' : ∀ q ∈ (r2 :: rs2), rowSentinel ∉ q := fun q hq => hall_no q (List.mem_cons_of_mem r hq)
      simp only [flattenNonEmpty, List.append_assoc, List.cons_append, List.nil_append]
      rw [tileRowsAux_append_no_sentinel r (rowSentinel :: flattenNonEmpty (r2 :: rs2)) [] hr_no]
      simp only [List.append_nil, tileRowsAux, beq_self_eq_true, ↓reduceIte, hr_rev_ne,
        Bool.false_eq_true, List.reverse_reverse]
      rw [ih hall_ne' hall_no']

/-- Theorem 1b: Round-trip `tileRows (flattenTileRows rows) = rows.filter (!·.isEmpty)`. -/
theorem tileRows_flattenTileRows
    (rows : List (List String))
    (hno : ∀ r ∈ rows, rowSentinel ∉ r) :
    tileRows (flattenTileRows rows) = rows.filter (fun r => !r.isEmpty) := by
  unfold tileRows flattenTileRows
  apply tileRowsAux_flattenNonEmpty
  · intro r hr
    have hmem := (List.mem_filter.mp hr).2
    cases r with
    | nil => simp at hmem
    | cons _ _ => simp
  · intro r hr
    exact hno r (List.mem_filter.mp hr).1

private theorem visibleTiles_tileRowsAux (curRev order : List String)
    (hcur : rowSentinel ∉ curRev) :
    (tileRowsAux curRev order).flatten = curRev.reverse ++ visibleTiles order := by
  induction order generalizing curRev with
  | nil =>
    simp only [tileRowsAux, visibleTiles, List.filter_nil, List.append_nil]
    cases curRev with
    | nil => simp
    | cons _ _ => simp
  | cons x xs ih =>
    simp only [tileRowsAux, visibleTiles, List.filter_cons]
    by_cases hx : x = rowSentinel
    · subst hx
      simp only [beq_self_eq_true, ↓reduceIte, bne_self_eq_false, Bool.false_eq_true]
      cases curRev with
      | nil =>
        simpa [visibleTiles] using ih [] (by simp)
      | cons c cs =>
        simp only [List.isEmpty_cons, Bool.false_eq_true, ↓reduceIte, List.flatten_cons]
        rw [ih [] (by simp)]
        simp [visibleTiles]
    · have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
      have hx_bne : (x != rowSentinel) = true := by simp [bne, hx_beq]
      have hcur' : rowSentinel ∉ x :: curRev := by
        intro hmem; simp at hmem
        rcases hmem with h1 | h2
        · exact hx h1.symm
        · exact hcur h2
      simp only [hx_beq, Bool.false_eq_true, ↓reduceIte, hx_bne]
      rw [ih (x :: curRev) hcur']
      simp [visibleTiles, List.append_assoc]

theorem flatten_tileRows_eq_visibleTiles (order : List String) :
    (tileRows order).flatten = visibleTiles order := by
  simpa [tileRows] using visibleTiles_tileRowsAux [] order (by simp)

private theorem visibleTiles_flattenNonEmpty
    (rows : List (List String))
    (hno : ∀ r ∈ rows, rowSentinel ∉ r) :
    visibleTiles (flattenNonEmpty rows) = rows.flatten := by
  induction rows with
  | nil => simp [flattenNonEmpty, visibleTiles]
  | cons r rs ih =>
    have hr_no : rowSentinel ∉ r := hno r (by simp)
    have hr_filt : visibleTiles r = r := by
      unfold visibleTiles
      apply List.filter_eq_self.mpr
      intro x hx
      have hne : x ≠ rowSentinel := fun h => hr_no (h ▸ hx)
      simp [bne, beq_false_of_ne hne]
    cases rs with
    | nil =>
      simpa [flattenNonEmpty] using hr_filt
    | cons r2 rs2 =>
      have hno' : ∀ q ∈ (r2 :: rs2), rowSentinel ∉ q := fun q hq => hno q (List.mem_cons_of_mem r hq)
      simp only [flattenNonEmpty, visibleTiles, List.filter_append, List.filter_cons,
        bne_self_eq_false, Bool.false_eq_true, ↓reduceIte, List.filter_nil]
      have hr_f : r.filter (fun s => s != rowSentinel) = r := hr_filt
      rw [hr_f]
      have hih := ih hno'
      unfold visibleTiles at hih
      rw [hih]
      simp

theorem visibleTiles_flattenTileRows
    (rows : List (List String))
    (hno : ∀ r ∈ rows, rowSentinel ∉ r) :
    visibleTiles (flattenTileRows rows) = rows.flatten := by
  unfold flattenTileRows
  rw [visibleTiles_flattenNonEmpty]
  · induction rows with
    | nil => simp
    | cons r rs ih =>
      cases r with
      | nil => simpa using ih (fun q hq => hno q (List.mem_cons_of_mem [] hq))
      | cons x xs =>
        simp [ih (fun q hq => hno q (List.mem_cons_of_mem (x :: xs) hq))]
  · intro r hr
    exact hno r (List.mem_filter.mp hr).1

-- ── 2. Move tile to new row ───────────────────────────────────────────────────

def removeFirst (target : String) : List String → List String
  | [] => []
  | x :: xs => if x == target then xs else x :: removeFirst target xs

def findRowIdxAux (target : String) (idx : Nat) : List (List String) → Option Nat
  | [] => none
  | r :: rs => if r.contains target then some idx else findRowIdxAux target (idx + 1) rs

def removeAtIdx : Nat → List α → List α
  | _, [] => []
  | 0, _ :: xs => xs
  | n + 1, x :: xs => x :: removeAtIdx n xs

def insertAtIdx (idx : Nat) (val : α) : List α → List α
  | [] => [val]
  | x :: xs =>
    match idx with
    | 0 => val :: x :: xs
    | n + 1 => x :: insertAtIdx n val xs

def replaceAtIdx (idx : Nat) (val : α) : List α → List α
  | [] => []
  | x :: xs =>
    match idx with
    | 0 => val :: xs
    | n + 1 => x :: replaceAtIdx n val xs

def moveTileToNewRow (tileOrder : List String) (tileId : String) (afterRowIdx : Int) : List String :=
  let rows := tileRows tileOrder
  match findRowIdxAux tileId 0 rows with
  | none => tileOrder
  | some sourceRowIdx =>
    let boundary0 : Nat :=
      if afterRowIdx < 0 then 0 else min (afterRowIdx.toNat + 1) rows.length
    let sourceRow := rows.getD sourceRowIdx []
    let updatedRow := removeFirst tileId sourceRow
    let (rows1, boundary1) :=
      if updatedRow.isEmpty then
        let b := if sourceRowIdx < boundary0 then boundary0 - 1 else boundary0
        (removeAtIdx sourceRowIdx rows, b)
      else
        (replaceAtIdx sourceRowIdx updatedRow rows, boundary0)
    let boundary := min boundary1 rows1.length
    let rows2 := insertAtIdx boundary [tileId] rows1
    flattenTileRows rows2

-- Helper lemmas for `moveTileToNewRow`

private theorem mem_removeFirst_iff {a x : String} {xs : List String}
    (hnodup : xs.Nodup) :
    x ∈ removeFirst a xs ↔ x ∈ xs ∧ x ≠ a := by
  induction xs with
  | nil => simp [removeFirst]
  | cons y ys ih =>
    rw [List.nodup_cons] at hnodup
    rcases hnodup with ⟨hy_not_in, hys_nodup⟩
    simp only [removeFirst]
    by_cases hya : y = a
    · subst hya
      simp only [beq_self_eq_true, ↓reduceIte, List.mem_cons]
      constructor
      · intro hx
        exact ⟨Or.inr hx, fun h => hy_not_in (h ▸ hx)⟩
      · rintro ⟨rfl | hx, hne⟩
        · exact absurd rfl hne
        · exact hx
    · have hbeq : (y == a) = false := beq_false_of_ne hya
      simp only [hbeq, Bool.false_eq_true, ↓reduceIte, List.mem_cons]
      rw [ih hys_nodup]
      constructor
      · rintro (rfl | ⟨hx, hne⟩)
        · exact ⟨Or.inl rfl, hya⟩
        · exact ⟨Or.inr hx, hne⟩
      · rintro ⟨rfl | hx, hne⟩
        · exact Or.inl rfl
        · exact Or.inr ⟨hx, hne⟩

private theorem mem_removeFirst_imp {a x : String} {xs : List String}
    (hx : x ∈ removeFirst a xs) : x ∈ xs := by
  induction xs with
  | nil => simp [removeFirst] at hx
  | cons y ys ih =>
    simp only [removeFirst] at hx
    split at hx
    · exact List.mem_cons_of_mem y hx
    · simp at hx
      rcases hx with rfl | hx'
      · exact List.mem_cons_self
      · exact List.mem_cons_of_mem y (ih hx')

private theorem findRowIdxAux_some_spec (target : String) (idx : Nat) (rows : List (List String))
    {k : Nat} (h : findRowIdxAux target idx rows = some k) :
    idx ≤ k ∧ k - idx < rows.length ∧ target ∈ rows.getD (k - idx) [] := by
  induction rows generalizing idx with
  | nil => simp [findRowIdxAux] at h
  | cons r rs ih =>
    simp only [findRowIdxAux] at h
    split at h
    · rename_i hcont
      simp at h
      subst h
      refine ⟨Nat.le_refl _, by simp, ?_⟩
      simpa [List.getD] using List.contains_iff_mem.mp hcont
    · have ⟨hle, hlt, hmem⟩ := ih (idx + 1) h
      have hsub : k - idx = (k - (idx + 1)) + 1 := by omega
      refine ⟨by omega, by simp; omega, ?_⟩
      rw [hsub]
      simpa [List.getD] using hmem

private theorem mem_removeAtIdx_imp {n : Nat} {x : α} {xs : List α}
    (h : x ∈ removeAtIdx n xs) : x ∈ xs := by
  induction xs generalizing n with
  | nil => simp [removeAtIdx] at h
  | cons y ys ih =>
    cases n with
    | zero => exact List.mem_cons_of_mem y h
    | succ m =>
      simp only [removeAtIdx, List.mem_cons] at h
      rcases h with rfl | h'
      · exact List.mem_cons_self
      · exact List.mem_cons_of_mem y (ih h')

private theorem mem_replaceAtIdx_imp {n : Nat} {val x : α} {xs : List α}
    (h : x ∈ replaceAtIdx n val xs) : x = val ∨ x ∈ xs := by
  induction xs generalizing n with
  | nil => simp [replaceAtIdx] at h
  | cons y ys ih =>
    cases n with
    | zero =>
      simp only [replaceAtIdx, List.mem_cons] at h
      rcases h with rfl | h'
      · exact Or.inl rfl
      · exact Or.inr (List.mem_cons_of_mem y h')
    | succ m =>
      simp only [replaceAtIdx, List.mem_cons] at h
      rcases h with rfl | h'
      · exact Or.inr List.mem_cons_self
      · rcases ih h' with rfl | h''
        · exact Or.inl rfl
        · exact Or.inr (List.mem_cons_of_mem y h'')

private theorem mem_insertAtIdx_iff {n : Nat} {val x : α} {xs : List α} :
    x ∈ insertAtIdx n val xs ↔ x = val ∨ x ∈ xs := by
  induction xs generalizing n with
  | nil => simp [insertAtIdx]
  | cons y ys ih =>
    cases n with
    | zero => simp [insertAtIdx]
    | succ m =>
      simp only [insertAtIdx, List.mem_cons, ih]
      grind

private theorem flatten_insertAtIdx_mem {n : Nat} {val : List α} {x : α} {xs : List (List α)} :
    x ∈ (insertAtIdx n val xs).flatten ↔ x ∈ val ∨ x ∈ xs.flatten := by
  simp only [List.mem_flatten, mem_insertAtIdx_iff]
  constructor
  · rintro ⟨l, rfl | hl, hx⟩
    · exact Or.inl hx
    · exact Or.inr ⟨l, hl, hx⟩
  · rintro (hx | ⟨l, hl, hx⟩)
    · exact ⟨val, Or.inl rfl, hx⟩
    · exact ⟨l, Or.inr hl, hx⟩

/-- Helper with `a ∈ rows.getD n []` hypothesis. -/
private theorem mem_flatten_removeAtIdx_of_empty
    {n : Nat} {a x : String} {rows : List (List String)}
    (hn : n < rows.length)
    (ha : a ∈ rows.getD n [])
    (hempty : removeFirst a (rows.getD n []) = [])
    (hnodup : rows.flatten.Nodup) :
    x ∈ (removeAtIdx n rows).flatten ↔ x ∈ rows.flatten ∧ x ≠ a := by
  induction rows generalizing n with
  | nil => simp at hn
  | cons r rs ih =>
    rw [List.flatten_cons, List.nodup_append] at hnodup
    rcases hnodup with ⟨hr_nodup, hrs_nodup, hdisj⟩
    cases n with
    | zero =>
      simp only [removeAtIdx, List.getD, List.getElem?_cons_zero, Option.getD_some] at ha hempty ⊢
      have hr_only_a : ∀ y ∈ r, y = a := by
        intro y hy
        by_cases hya : y = a
        · exact hya
        · have hmem : y ∈ removeFirst a r := (mem_removeFirst_iff hr_nodup).mpr ⟨hy, hya⟩
          rw [hempty] at hmem
          simp at hmem
      constructor
      · intro hx
        refine ⟨by simp [hx], ?_⟩
        intro hxa
        subst hxa
        exact hdisj x ha x hx rfl
      · rintro ⟨hmem, hne⟩
        simp only [List.flatten_cons, List.mem_append] at hmem
        rcases hmem with hr | hrs
        · exact absurd (hr_only_a x hr) hne
        · exact hrs
    | succ m =>
      simp only [removeAtIdx, List.flatten_cons, List.mem_append]
      simp only [List.getD, List.getElem?_cons_succ] at ha hempty
      have hm : m < rs.length := by simp at hn; omega
      have hih := ih hm ha hempty hrs_nodup
      have ha_in_rs : a ∈ rs.flatten := by
        have hget : rs[m]'hm ∈ rs := List.getElem_mem hm
        have hgd : rs[m]?.getD [] = rs[m]'hm := by
          simp [List.getElem?_eq_getElem hm]
        rw [hgd] at ha
        exact List.mem_flatten.mpr ⟨rs[m]'hm, hget, ha⟩
      constructor
      · rintro (hr | hrs)
        · refine ⟨Or.inl hr, ?_⟩
          intro hxa
          subst hxa
          exact hdisj x hr x ha_in_rs rfl
        · have ⟨h1, h2⟩ := hih.mp hrs
          exact ⟨Or.inr h1, h2⟩
      · rintro ⟨hr | hrs, hne⟩
        · exact Or.inl hr
        · exact Or.inr (hih.mpr ⟨hrs, hne⟩)

private theorem mem_flatten_replaceAtIdx_removeFirst
    {n : Nat} {a x : String} {rows : List (List String)}
    (hn : n < rows.length)
    (ha : a ∈ rows.getD n [])
    (hnodup : rows.flatten.Nodup) :
    x ∈ (replaceAtIdx n (removeFirst a (rows.getD n [])) rows).flatten ↔
      x ∈ rows.flatten ∧ x ≠ a := by
  induction rows generalizing n with
  | nil => simp at hn
  | cons r rs ih =>
    rw [List.flatten_cons, List.nodup_append] at hnodup
    rcases hnodup with ⟨hr_nodup, hrs_nodup, hdisj⟩
    cases n with
    | zero =>
      simp only [replaceAtIdx, List.getD, List.getElem?_cons_zero, Option.getD_some,
        List.flatten_cons, List.mem_append] at ha ⊢
      rw [mem_removeFirst_iff hr_nodup]
      constructor
      · rintro (⟨hr, hne⟩ | hrs)
        · exact ⟨Or.inl hr, hne⟩
        · refine ⟨Or.inr hrs, ?_⟩
          intro hxa; subst hxa
          exact hdisj x ha x hrs rfl
      · rintro ⟨hr | hrs, hne⟩
        · exact Or.inl ⟨hr, hne⟩
        · exact Or.inr hrs
    | succ m =>
      simp only [replaceAtIdx, List.flatten_cons, List.mem_append]
      simp only [List.getD, List.getElem?_cons_succ] at ha
      have hm : m < rs.length := by simp at hn; omega
      have hih := ih hm ha hrs_nodup
      have ha_in_rs : a ∈ rs.flatten := by
        have hget : rs[m]'hm ∈ rs := List.getElem_mem hm
        have hgd : rs[m]?.getD [] = rs[m]'hm := by
          simp [List.getElem?_eq_getElem hm]
        rw [hgd] at ha
        exact List.mem_flatten.mpr ⟨rs[m]'hm, hget, ha⟩
      constructor
      · rintro (hr | hrs)
        · refine ⟨Or.inl hr, ?_⟩
          intro hxa; subst hxa
          exact hdisj x hr x ha_in_rs rfl
        · have ⟨h1, h2⟩ := hih.mp hrs
          exact ⟨Or.inr h1, h2⟩
      · rintro ⟨hr | hrs, hne⟩
        · exact Or.inl hr
        · exact Or.inr (hih.mpr ⟨hrs, hne⟩)

/-- Theorem 5a: `moveTileToNewRow` preserves canonical normal form whenever the input is canonical. -/
theorem canonical_moveTileToNewRow
    (order : List String) (tileId : String) (afterRowIdx : Int)
    (hcan : CanonicalOrder order) :
    CanonicalOrder (moveTileToNewRow order tileId afterRowIdx) := by
  unfold moveTileToNewRow
  dsimp only
  cases hfind : findRowIdxAux tileId 0 (tileRows order) with
  | none => exact hcan
  | some sourceRowIdx =>
    have ⟨_, hlt, hmem⟩ := findRowIdxAux_some_spec tileId 0 (tileRows order) hfind
    simp only [Nat.sub_zero] at hlt hmem
    have hrows_no := tileRows_no_sentinel order
    have htid_ne : tileId ≠ rowSentinel := by
      have hget : (tileRows order)[sourceRowIdx]'hlt ∈ tileRows order := List.getElem_mem hlt
      have hgd : (tileRows order).getD sourceRowIdx [] = (tileRows order)[sourceRowIdx]'hlt := by
        simp [List.getD, List.getElem?_eq_getElem hlt]
      rw [hgd] at hmem
      intro heq
      exact hrows_no _ hget (heq ▸ hmem)
    dsimp only
    split
    · dsimp only
      apply canonical_flattenTileRows
      intro r hr
      rw [mem_insertAtIdx_iff] at hr
      rcases hr with rfl | hr1
      · intro h; simp at h; exact htid_ne h.symm
      · exact hrows_no r (mem_removeAtIdx_imp hr1)
    · dsimp only
      apply canonical_flattenTileRows
      intro r hr
      rw [mem_insertAtIdx_iff] at hr
      rcases hr with rfl | hr1
      · intro h; simp at h; exact htid_ne h.symm
      · rcases mem_replaceAtIdx_imp hr1 with rfl | hr2
        · intro h
          have hmem_src := mem_removeFirst_imp h
          have hget : (tileRows order)[sourceRowIdx]'hlt ∈ tileRows order := List.getElem_mem hlt
          have hgd : (tileRows order).getD sourceRowIdx [] = (tileRows order)[sourceRowIdx]'hlt := by
            simp [List.getD, List.getElem?_eq_getElem hlt]
          rw [hgd] at hmem_src
          exact hrows_no _ hget hmem_src
        · exact hrows_no r hr2

/-- Theorem 5b: `moveTileToNewRow` preserves the exact set of visible tile IDs. -/
theorem mem_visibleTiles_moveTileToNewRow
    (order : List String) (tileId : String) (afterRowIdx : Int)
    (hnodup : (visibleTiles order).Nodup) :
    ∀ x, x ∈ visibleTiles (moveTileToNewRow order tileId afterRowIdx) ↔ x ∈ visibleTiles order := by
  intro x
  unfold moveTileToNewRow
  dsimp only
  cases hfind : findRowIdxAux tileId 0 (tileRows order) with
  | none => exact Iff.rfl
  | some sourceRowIdx =>
    have ⟨_, hlt, hmem⟩ := findRowIdxAux_some_spec tileId 0 (tileRows order) hfind
    simp only [Nat.sub_zero] at hlt hmem
    have hrows_no := tileRows_no_sentinel order
    have hflat_eq : (tileRows order).flatten = visibleTiles order :=
      flatten_tileRows_eq_visibleTiles order
    have hrows_nodup : (tileRows order).flatten.Nodup := hflat_eq ▸ hnodup
    have htid_in_flat : tileId ∈ (tileRows order).flatten := by
      have hget : (tileRows order)[sourceRowIdx]'hlt ∈ tileRows order := List.getElem_mem hlt
      have hgd : (tileRows order).getD sourceRowIdx [] = (tileRows order)[sourceRowIdx]'hlt := by
        simp [List.getD, List.getElem?_eq_getElem hlt]
      rw [hgd] at hmem
      exact List.mem_flatten.mpr ⟨_, hget, hmem⟩
    have htid_ne : tileId ≠ rowSentinel := by
      have hget : (tileRows order)[sourceRowIdx]'hlt ∈ tileRows order := List.getElem_mem hlt
      have hgd : (tileRows order).getD sourceRowIdx [] = (tileRows order)[sourceRowIdx]'hlt := by
        simp [List.getD, List.getElem?_eq_getElem hlt]
      rw [hgd] at hmem
      intro heq; exact hrows_no _ hget (heq ▸ hmem)
    dsimp only
    split
    · rename_i hempty_b
      dsimp only
      have hempty : removeFirst tileId ((tileRows order).getD sourceRowIdx []) = [] := by
        simpa [List.getD] using hempty_b
      rw [visibleTiles_flattenTileRows]
      · rw [flatten_insertAtIdx_mem, ← hflat_eq,
          mem_flatten_removeAtIdx_of_empty hlt hmem hempty hrows_nodup]
        simp only [List.mem_singleton]
        constructor
        · rintro (rfl | ⟨hx, _⟩)
          · exact htid_in_flat
          · exact hx
        · intro hx
          by_cases hxt : x = tileId
          · exact Or.inl hxt
          · exact Or.inr ⟨hx, hxt⟩
      · intro r hr
        rw [mem_insertAtIdx_iff] at hr
        rcases hr with rfl | hr1
        · intro h; simp at h; exact htid_ne h.symm
        · exact hrows_no r (mem_removeAtIdx_imp hr1)
    · dsimp only
      rw [visibleTiles_flattenTileRows]
      · rw [flatten_insertAtIdx_mem, ← hflat_eq,
          mem_flatten_replaceAtIdx_removeFirst hlt hmem hrows_nodup]
        simp only [List.mem_singleton]
        constructor
        · rintro (rfl | ⟨hx, _⟩)
          · exact htid_in_flat
          · exact hx
        · intro hx
          by_cases hxt : x = tileId
          · exact Or.inl hxt
          · exact Or.inr ⟨hx, hxt⟩
      · intro r hr
        rw [mem_insertAtIdx_iff] at hr
        rcases hr with rfl | hr1
        · intro h; simp at h; exact htid_ne h.symm
        · rcases mem_replaceAtIdx_imp hr1 with rfl | hr2
          · intro h
            have hmem_src := mem_removeFirst_imp h
            have hget : (tileRows order)[sourceRowIdx]'hlt ∈ tileRows order := List.getElem_mem hlt
            have hgd : (tileRows order).getD sourceRowIdx [] = (tileRows order)[sourceRowIdx]'hlt := by
              simp [List.getD, List.getElem?_eq_getElem hlt]
            rw [hgd] at hmem_src
            exact hrows_no _ hget hmem_src
          · exact hrows_no r hr2

-- ── 3. Layout anchor & migration merge ────────────────────────────────────────

private def isAsciiDigit (c : Char) : Bool :=
  '0' ≤ c && c ≤ '9'

private def parseGpuAnchor (tileId : String) : Option (String × Bool) :=
  if !tileId.startsWith "gpu_" then none
  else
    let rest := tileId.drop 4
    if rest.endsWith "_total" then
      let digits := (rest.dropEnd 6).toString
      if !digits.isEmpty && digits.all isAsciiDigit then
        some ("gpu_" ++ digits ++ "_3d", true)
      else none
    else if rest.endsWith "_power" then
      let digits := (rest.dropEnd 6).toString
      if !digits.isEmpty && digits.all isAsciiDigit then
        some ("gpu_" ++ digits ++ "_vram", false)
      else none
    else none

def layoutAnchor (tileId : String) : Option (String × Bool) :=
  if tileId == "cpu_power" then
    some ("cpu_total", false)
  else
    parseGpuAnchor tileId

/-- Insert `tid` immediately before or after the first occurrence of `anchor`. -/
def insertNearAnchor (anchor tid : String) (insertBefore : Bool) : List String → List String
  | [] => [tid]
  | x :: xs =>
    if x == anchor then
      if insertBefore then tid :: x :: xs else x :: tid :: xs
    else
      x :: insertNearAnchor anchor tid insertBefore xs

def cleanSavedOrderAux (validIds : List String) (lastWasBreak : Bool)
    (seen : List String) : List String → List String
  | [] => []
  | item :: rest =>
    if item == rowSentinel then
      if !lastWasBreak then
        rowSentinel :: cleanSavedOrderAux validIds true seen rest
      else
        cleanSavedOrderAux validIds true seen rest
    else if validIds.contains item && !seen.contains item then
      item :: cleanSavedOrderAux validIds false (item :: seen) rest
    else
      cleanSavedOrderAux validIds lastWasBreak seen rest

def stripTrailingSentinels (xs : List String) : List String :=
  (xs.reverse.dropWhile (fun s => s == rowSentinel)).reverse

def cleanSavedOrder (savedOrder validIds : List String) : List String :=
  stripTrailingSentinels (cleanSavedOrderAux validIds true [] savedOrder)

def cleanSavedHiddenAux (validIds visible seen : List String) : List String → List String
  | [] => []
  | item :: rest =>
    if validIds.contains item && !visible.contains item && !seen.contains item then
      item :: cleanSavedHiddenAux validIds visible (item :: seen) rest
    else
      cleanSavedHiddenAux validIds visible seen rest

def cleanSavedHidden (savedHidden validIds order : List String) : List String :=
  cleanSavedHiddenAux validIds (visibleTiles order) [] savedHidden

def findVisibleDependent (tileIds order : List String) (anchorId : String) : Option (String × Bool) :=
  match tileIds.find? (fun cand =>
    order.contains cand &&
    match layoutAnchor cand with
    | some (a, _) => a == anchorId
    | none => false) with
  | none => none
  | some dep =>
    match layoutAnchor dep with
    | some (_, depInsertBefore) => some (dep, depInsertBefore)
    | none => none

/-- Pass 1 of `_merge_layout_tiles`: restore missing non-anchored tiles and collect anchored ones. -/
def mergePass1 (allTileIds defaultIds : List String) :
    List String → List String → List String → List String → Bool →
      (List String × List String × List String × Bool)
  | [], order, hidden, anchoredRev, changed =>
    (order, hidden, anchoredRev.reverse, changed)
  | tid :: rest, order, hidden, anchoredRev, changed =>
    if order.contains tid || hidden.contains tid then
      mergePass1 allTileIds defaultIds rest order hidden anchoredRev changed
    else if (layoutAnchor tid).isSome then
      mergePass1 allTileIds defaultIds rest order hidden (tid :: anchoredRev) changed
    else if defaultIds.contains tid then
      let order' :=
        match findVisibleDependent allTileIds order tid with
        | none => order ++ [tid]
        | some (dep, depInsertBefore) =>
          -- Python: insert_after = bool(depInsertBefore) -> insertBefore = !depInsertBefore
          insertNearAnchor dep tid (!depInsertBefore) order
      mergePass1 allTileIds defaultIds rest order' hidden anchoredRev true
    else
      mergePass1 allTileIds defaultIds rest order (hidden ++ [tid]) anchoredRev true

/-- Pass 2 of `_merge_layout_tiles`: place anchored tiles beside their visible anchor or hide them. -/
def mergePass2 : List String → List String → List String → Bool → (List String × List String × Bool)
  | [], order, hidden, changed => (order, hidden, changed)
  | tid :: rest, order, hidden, changed =>
    match layoutAnchor tid with
    | none => mergePass2 rest order hidden changed
    | some (anchor, insertBefore) =>
      if order.contains anchor then
        let order' := insertNearAnchor anchor tid insertBefore order
        mergePass2 rest order' hidden true
      else
        mergePass2 rest order (hidden ++ [tid]) true

def mergeLayoutTiles
    (savedOrder savedHidden tileIds defaultOrder : List String) :
    (List String × List String × Bool) :=
  let order0 := cleanSavedOrder savedOrder tileIds
  let hidden0 := cleanSavedHidden savedHidden tileIds order0
  let changed0 := (order0 != savedOrder) || (hidden0 != savedHidden)
  let defaultIds := visibleTiles defaultOrder
  let (order1, hidden1, anchored, changed1) :=
    mergePass1 tileIds defaultIds tileIds order0 hidden0 [] changed0
  mergePass2 anchored order1 hidden1 changed1

-- ── Proofs for mergeLayoutTiles ───────────────────────────────────────────────

/-- A layout state `(order, hidden)` is well-formed w.r.t. `validIds` when:
    1. `order` is in canonical normal form,
    2. `visibleTiles order` is duplicate-free and subset of `validIds`,
    3. `hidden` is duplicate-free, subset of `validIds`, contains no sentinel,
    4. `visibleTiles order` and `hidden` are disjoint. -/
structure ValidLayoutState (validIds order hidden : List String) : Prop where
  canonical : CanonicalOrder order
  visNodup : (visibleTiles order).Nodup
  visSub : ∀ x ∈ visibleTiles order, x ∈ validIds
  hidNodup : hidden.Nodup
  hidSub : ∀ x ∈ hidden, x ∈ validIds
  hidNoSent : rowSentinel ∉ hidden
  disjoint : ∀ x, x ∈ visibleTiles order → x ∉ hidden

private theorem mem_visibleTiles_iff {x : String} {order : List String} :
    x ∈ visibleTiles order ↔ x ∈ order ∧ x ≠ rowSentinel := by
  simp [visibleTiles, bne]

private theorem isCanonicalAux_append_nonSentinel
    (xs : List String) (t : String) (ht : t ≠ rowSentinel) (b : Bool)
    (hcan : isCanonicalAux b xs = true) :
    isCanonicalAux b (xs ++ [t]) = true := by
  induction xs generalizing b with
  | nil =>
    have ht_bne : (t != rowSentinel) = true := by simp [bne, beq_false_of_ne ht]
    simp [isCanonicalAux, ht_bne]
  | cons x rest ih =>
    cases rest with
    | nil =>
      simp only [isCanonicalAux] at hcan
      have hx : x ≠ rowSentinel := by
        intro heq; subst heq; simp at hcan
      have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
      have ht_bne : (t != rowSentinel) = true := by simp [bne, beq_false_of_ne ht]
      simp [isCanonicalAux, hx_beq, ht_bne]
    | cons y ys =>
      have hcons : (x :: y :: ys) ++ [t] = x :: ((y :: ys) ++ [t]) := rfl
      rw [hcons]
      have htail_ne : ((y :: ys) ++ [t]) ≠ [] := by simp
      cases htail : ((y :: ys) ++ [t]) with
      | nil => exact absurd htail htail_ne
      | cons z zs =>
        simp only [isCanonicalAux] at hcan ⊢
        split at hcan
        · rename_i hxs
          simp only [hxs, ↓reduceIte]
          split at hcan
          · contradiction
          · rename_i hb
            simp only [hb, Bool.false_eq_true, ↓reduceIte]
            rw [← htail]
            exact ih true hcan
        · rename_i hxs
          simp only [hxs, Bool.false_eq_true, ↓reduceIte]
          rw [← htail]
          exact ih false hcan

private theorem canonical_append_tile
    (order : List String) (t : String) (ht : t ≠ rowSentinel)
    (hcan : CanonicalOrder order) :
    CanonicalOrder (order ++ [t]) := by
  unfold CanonicalOrder at hcan ⊢
  cases order with
  | nil =>
    have ht_bne : (t != rowSentinel) = true := by simp [bne, beq_false_of_ne ht]
    simp [isCanonicalOrder, isCanonicalAux, ht_bne]
  | cons x xs =>
    simp only [isCanonicalOrder] at hcan
    have h := isCanonicalAux_append_nonSentinel (x :: xs) t ht true hcan
    simpa [isCanonicalOrder] using h

private theorem isCanonicalAux_insertNearAnchor
    (xs : List String) (anchor tid : String) (insertBefore : Bool)
    (hanc : anchor ≠ rowSentinel) (htid : tid ≠ rowSentinel)
    (b : Bool) (hcan : isCanonicalAux b xs = true) :
    isCanonicalAux b (insertNearAnchor anchor tid insertBefore xs) = true := by
  have htid_beq : (tid == rowSentinel) = false := beq_false_of_ne htid
  have htid_bne : (tid != rowSentinel) = true := by simp [bne, htid_beq]
  have hanc_beq : (anchor == rowSentinel) = false := beq_false_of_ne hanc
  have hanc_bne : (anchor != rowSentinel) = true := by simp [bne, hanc_beq]
  induction xs generalizing b with
  | nil =>
    simp [insertNearAnchor, isCanonicalAux, htid_bne]
  | cons x rest ih =>
    by_cases hxa : x = anchor
    · subst hxa
      simp only [insertNearAnchor, beq_self_eq_true, ↓reduceIte]
      cases insertBefore with
      | true =>
        cases rest with
        | nil =>
          simp [isCanonicalAux, htid_beq, hanc_bne]
        | cons y ys =>
          simp only [isCanonicalAux, hanc_beq, Bool.false_eq_true, ↓reduceIte] at hcan
          simp [isCanonicalAux, htid_beq, hanc_beq, hcan]
      | false =>
        cases rest with
        | nil =>
          simp [isCanonicalAux, hanc_beq, htid_bne]
        | cons y ys =>
          simp only [isCanonicalAux, hanc_beq, Bool.false_eq_true, ↓reduceIte] at hcan
          simp [isCanonicalAux, hanc_beq, htid_beq, hcan]
    · have hxa_beq : (x == anchor) = false := beq_false_of_ne hxa
      simp only [insertNearAnchor, hxa_beq, Bool.false_eq_true, ↓reduceIte]
      cases rest with
      | nil =>
        simp only [isCanonicalAux] at hcan
        have hx : x ≠ rowSentinel := by intro h; subst h; simp at hcan
        have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
        simp [insertNearAnchor, isCanonicalAux, hx_beq, htid_bne]
      | cons y ys =>
        have hne : insertNearAnchor anchor tid insertBefore (y :: ys) ≠ [] := by
          unfold insertNearAnchor
          split
          · cases insertBefore <;> simp
          · simp
        cases hins : insertNearAnchor anchor tid insertBefore (y :: ys) with
        | nil => exact absurd hins hne
        | cons z zs =>
          simp only [isCanonicalAux] at hcan ⊢
          split at hcan
          · rename_i hxs
            simp only [hxs, ↓reduceIte]
            split at hcan
            · contradiction
            · rename_i hb
              simp only [hb, Bool.false_eq_true, ↓reduceIte]
              rw [← hins]
              exact ih true hcan
          · rename_i hxs
            simp only [hxs, Bool.false_eq_true, ↓reduceIte]
            rw [← hins]
            exact ih false hcan

private theorem canonical_insertNearAnchor
    (order : List String) (anchor tid : String) (insertBefore : Bool)
    (hanc : anchor ≠ rowSentinel) (htid : tid ≠ rowSentinel)
    (hcan : CanonicalOrder order) :
    CanonicalOrder (insertNearAnchor anchor tid insertBefore order) := by
  unfold CanonicalOrder at hcan ⊢
  cases order with
  | nil =>
    have htid_bne : (tid != rowSentinel) = true := by simp [bne, beq_false_of_ne htid]
    simp [insertNearAnchor, isCanonicalOrder, isCanonicalAux, htid_bne]
  | cons x xs =>
    simp only [isCanonicalOrder] at hcan
    have hne : insertNearAnchor anchor tid insertBefore (x :: xs) ≠ [] := by
      unfold insertNearAnchor
      split
      · cases insertBefore <;> simp
      · simp
    cases hins : insertNearAnchor anchor tid insertBefore (x :: xs) with
    | nil => exact absurd hins hne
    | cons z zs =>
      rw [← hins]
      have h := isCanonicalAux_insertNearAnchor (x :: xs) anchor tid insertBefore hanc htid true hcan
      simpa [isCanonicalOrder, hins] using h

private theorem mem_insertNearAnchor_iff
    (anchor tid x : String) (insertBefore : Bool) (xs : List String) :
    x ∈ insertNearAnchor anchor tid insertBefore xs ↔ x = tid ∨ x ∈ xs := by
  induction xs with
  | nil => simp [insertNearAnchor]
  | cons y ys ih =>
    simp only [insertNearAnchor]
    split
    · cases insertBefore <;> simp [List.mem_cons] <;> grind
    · simp only [List.mem_cons, ih]
      grind

private theorem visibleTiles_insertNearAnchor_nodup
    (anchor tid : String) (insertBefore : Bool) (xs : List String)
    (htid : tid ≠ rowSentinel) (hnotin : tid ∉ visibleTiles xs)
    (hnodup : (visibleTiles xs).Nodup) :
    (visibleTiles (insertNearAnchor anchor tid insertBefore xs)).Nodup := by
  have htid_bne : (tid != rowSentinel) = true := by simp [bne, beq_false_of_ne htid]
  induction xs with
  | nil =>
    simp [insertNearAnchor, visibleTiles, htid_bne]
  | cons y ys ih =>
    simp only [insertNearAnchor]
    by_cases hya : y = anchor
    · subst hya
      simp only [beq_self_eq_true, ↓reduceIte]
      have htid_not_y : tid ≠ y := by
        intro h; subst h
        apply hnotin
        simp [visibleTiles, htid_bne]
      cases insertBefore with
      | true =>
        simp only [↓reduceIte]
        have hvt : visibleTiles (tid :: y :: ys) = tid :: visibleTiles (y :: ys) := by
          simp [visibleTiles, htid_bne]
        rw [hvt, List.nodup_cons]
        exact ⟨hnotin, hnodup⟩
      | false =>
        simp only [Bool.false_eq_true, ↓reduceIte]
        by_cases hy : y = rowSentinel
        · subst hy
          simp only [visibleTiles, List.filter_cons, bne_self_eq_false, Bool.false_eq_true,
            ↓reduceIte, htid_bne] at hnotin hnodup ⊢
          rw [List.nodup_cons]
          exact ⟨hnotin, hnodup⟩
        · have hy_bne : (y != rowSentinel) = true := by simp [bne, beq_false_of_ne hy]
          simp only [visibleTiles, List.filter_cons, hy_bne, ↓reduceIte] at hnotin hnodup
          simp only [visibleTiles, List.filter_cons, hy_bne, htid_bne, ↓reduceIte]
          rw [List.nodup_cons] at hnodup
          rcases hnodup with ⟨hy_not_ys, hys_nodup⟩
          rw [List.mem_cons] at hnotin
          have htid_not_ys : tid ∉ ys.filter (fun s => s != rowSentinel) :=
            fun h => hnotin (Or.inr h)
          rw [List.nodup_cons, List.nodup_cons]
          refine ⟨?_, htid_not_ys, hys_nodup⟩
          intro hmem
          rw [List.mem_cons] at hmem
          rcases hmem with hy_eq_tid | hy_in_ys
          · exact htid_not_y hy_eq_tid.symm
          · exact hy_not_ys hy_in_ys
    · have hya_beq : (y == anchor) = false := beq_false_of_ne hya
      simp only [hya_beq, Bool.false_eq_true, ↓reduceIte]
      by_cases hy : y = rowSentinel
      · subst hy
        simp only [visibleTiles, List.filter_cons, bne_self_eq_false, Bool.false_eq_true,
          ↓reduceIte] at hnotin hnodup ⊢
        exact ih hnotin hnodup
      · have hy_bne : (y != rowSentinel) = true := by simp [bne, beq_false_of_ne hy]
        simp only [visibleTiles, List.filter_cons, hy_bne, ↓reduceIte] at hnotin hnodup ⊢
        rw [List.nodup_cons] at hnodup ⊢
        rcases hnodup with ⟨hy_not_ys, hys_nodup⟩
        rw [List.mem_cons] at hnotin
        have htid_ne_y : tid ≠ y := fun h => hnotin (Or.inl h)
        have htid_not_ys : tid ∉ ys.filter (fun s => s != rowSentinel) :=
          fun h => hnotin (Or.inr h)
        refine ⟨?_, ih htid_not_ys hys_nodup⟩
        intro hy_in
        have hy_mem := (mem_visibleTiles_iff.mp hy_in).1
        rw [mem_insertNearAnchor_iff] at hy_mem
        rcases hy_mem with hy_eq_tid | hy_in_ys
        · exact htid_ne_y hy_eq_tid.symm
        · exact hy_not_ys (mem_visibleTiles_iff.mpr ⟨hy_in_ys, hy⟩)

-- Clean saved order & hidden proofs

/-- Stronger structural invariant on `cleanSavedOrderAux`: no leading sentinel if `b = true`,
    no consecutive sentinels anywhere. -/
private def noBadBreaks (lastWasBreak : Bool) : List String → Bool
  | [] => true
  | x :: rest =>
    if x == rowSentinel then
      if lastWasBreak then false else noBadBreaks true rest
    else
      noBadBreaks false rest

private theorem cleanSavedOrderAux_noBadBreaks
    (validIds : List String) (b : Bool) (seen xs : List String) :
    noBadBreaks b (cleanSavedOrderAux validIds b seen xs) = true := by
  induction xs generalizing b seen with
  | nil => simp [cleanSavedOrderAux, noBadBreaks]
  | cons x rest ih =>
    simp only [cleanSavedOrderAux]
    by_cases hx : x = rowSentinel
    · subst hx
      simp only [beq_self_eq_true, ↓reduceIte]
      cases b with
      | true =>
        simp only [Bool.not_true, Bool.false_eq_true, ↓reduceIte]
        exact ih true seen
      | false =>
        simp only [Bool.not_false, ↓reduceIte, noBadBreaks, beq_self_eq_true, Bool.false_eq_true]
        exact ih true seen
    · have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
      simp only [hx_beq, Bool.false_eq_true, ↓reduceIte]
      split
      · simp only [noBadBreaks, hx_beq, Bool.false_eq_true, ↓reduceIte]
        exact ih false (x :: seen)
      · exact ih b seen

private theorem noBadBreaks_stripTrailing_aux
    (b : Bool) (xs : List String)
    (hnb : noBadBreaks b xs = true)
    (hne : xs ≠ [])
    (hlast : xs.getLast hne ≠ rowSentinel) :
    isCanonicalAux b xs = true := by
  induction xs generalizing b with
  | nil => contradiction
  | cons x rest ih =>
    cases rest with
    | nil =>
      simp only [List.getLast_singleton] at hlast
      have hx_bne : (x != rowSentinel) = true := by simp [bne, beq_false_of_ne hlast]
      simp [isCanonicalAux, hx_bne]
    | cons y ys =>
      have hlast' : (y :: ys).getLast (by simp) ≠ rowSentinel := by
        simpa [List.getLast_cons] using hlast
      simp only [noBadBreaks] at hnb
      simp only [isCanonicalAux]
      split at hnb
      · rename_i hxs
        simp only [hxs, ↓reduceIte]
        split at hnb
        · contradiction
        · rename_i hb
          simp only [hb, Bool.false_eq_true, ↓reduceIte]
          exact ih true hnb (by simp) hlast'
      · rename_i hxs
        simp only [hxs, Bool.false_eq_true, ↓reduceIte]
        exact ih false hnb (by simp) hlast'

private theorem noBadBreaks_prefix
    (b : Bool) (xs ys : List String)
    (hnb : noBadBreaks b (xs ++ ys) = true) :
    noBadBreaks b xs = true := by
  induction xs generalizing b with
  | nil => simp [noBadBreaks]
  | cons x rest ih =>
    simp only [List.cons_append, noBadBreaks] at hnb ⊢
    split at hnb
    · rename_i hxs
      simp only [hxs, ↓reduceIte]
      split at hnb
      · contradiction
      · rename_i hb
        simp only [hb, Bool.false_eq_true, ↓reduceIte]
        exact ih true hnb
    · rename_i hxs
      simp only [hxs, Bool.false_eq_true, ↓reduceIte]
      exact ih false hnb

private theorem dropWhile_head_false (p : α → Bool) (xs : List α) {z : α} {zs : List α}
    (h : xs.dropWhile p = z :: zs) : p z = false := by
  induction xs with
  | nil => simp at h
  | cons a as ih =>
    simp only [List.dropWhile_cons] at h
    split at h
    · exact ih h
    · rename_i hpa
      simp only [List.cons.injEq] at h
      rcases h with ⟨rfl, _⟩
      cases hp : p a with
      | false => rfl
      | true => exact absurd hp hpa

private theorem stripTrailingSentinels_split (xs : List String) :
    ∃ tail, xs = stripTrailingSentinels xs ++ tail ∧
      ∀ hne : stripTrailingSentinels xs ≠ [],
        (stripTrailingSentinels xs).getLast hne ≠ rowSentinel := by
  unfold stripTrailingSentinels
  let p := fun s : String => s == rowSentinel
  have hrev := List.takeWhile_append_dropWhile (p := p) (l := xs.reverse)
  refine ⟨(xs.reverse.takeWhile p).reverse, ?_, ?_⟩
  · have := congrArg List.reverse hrev
    simp only [List.reverse_append, List.reverse_reverse] at this
    exact this.symm
  · intro hne
    have hdw_spec : ∀ ys : List String, ys = xs.reverse.dropWhile p →
        ∀ hne' : ys.reverse ≠ [], ys.reverse.getLast hne' ≠ rowSentinel := by
      intro ys hys hne'
      cases ys with
      | nil => simp at hne'
      | cons z zs =>
        have hz_false : p z = false := dropWhile_head_false p xs.reverse hys.symm
        have hgl : (z :: zs).reverse.getLast hne' = z := by
          simp [List.reverse_cons]
        rw [hgl]
        intro heq
        have hpz : p z = true := by simp [p, heq]
        rw [hz_false] at hpz
        contradiction
    exact hdw_spec _ rfl hne

private theorem canonical_cleanSavedOrder
    (savedOrder validIds : List String) :
    CanonicalOrder (cleanSavedOrder savedOrder validIds) := by
  unfold CanonicalOrder cleanSavedOrder
  have hnb := cleanSavedOrderAux_noBadBreaks validIds true [] savedOrder
  have ⟨tail, hsplit, hlast⟩ := stripTrailingSentinels_split
    (cleanSavedOrderAux validIds true [] savedOrder)
  rw [hsplit] at hnb
  have hnb_pre := noBadBreaks_prefix true _ tail hnb
  cases hres : stripTrailingSentinels (cleanSavedOrderAux validIds true [] savedOrder) with
  | nil => simp [isCanonicalOrder]
  | cons x xs =>
    have hne : stripTrailingSentinels (cleanSavedOrderAux validIds true [] savedOrder) ≠ [] := by
      simp [hres]
    have hne_cons : (x :: xs) ≠ [] := by simp
    have hlast' : (x :: xs).getLast hne_cons ≠ rowSentinel := by
      have := hlast hne
      simpa [hres] using this
    rw [hres] at hnb_pre
    have hcan := noBadBreaks_stripTrailing_aux true (x :: xs) hnb_pre hne_cons hlast'
    simpa [isCanonicalOrder] using hcan

private theorem cleanSavedOrderAux_mem_spec
    (validIds : List String) (b : Bool) (seen xs : List String) :
    (∀ x ∈ visibleTiles (cleanSavedOrderAux validIds b seen xs),
      x ∈ validIds ∧ x ∉ seen) ∧
    (visibleTiles (cleanSavedOrderAux validIds b seen xs)).Nodup := by
  induction xs generalizing b seen with
  | nil => simp [cleanSavedOrderAux, visibleTiles]
  | cons x rest ih =>
    simp only [cleanSavedOrderAux]
    by_cases hx : x = rowSentinel
    · subst hx
      simp only [beq_self_eq_true, ↓reduceIte]
      cases b with
      | true => simpa using ih true seen
      | false =>
        simp only [Bool.not_false, ↓reduceIte, visibleTiles, List.filter_cons,
          bne_self_eq_false, Bool.false_eq_true]
        exact ih true seen
    · have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
      have hx_bne : (x != rowSentinel) = true := by simp [bne, hx_beq]
      simp only [hx_beq, Bool.false_eq_true, ↓reduceIte]
      split
      · rename_i hcond
        simp only [Bool.and_eq_true, Bool.not_eq_true'] at hcond
        have hx_val : x ∈ validIds := List.contains_iff_mem.mp hcond.1
        have hx_not_seen : x ∉ seen := fun h => by
          have := List.contains_iff_mem.mpr h
          rw [hcond.2] at this; contradiction
        have ⟨hmem, hnd⟩ := ih false (x :: seen)
        simp only [visibleTiles, List.filter_cons, hx_bne, ↓reduceIte]
        constructor
        · intro y hy
          simp only [List.mem_cons] at hy
          rcases hy with rfl | hy'
          · exact ⟨hx_val, hx_not_seen⟩
          · have ⟨hv, hns⟩ := hmem y hy'
            exact ⟨hv, fun h => hns (List.mem_cons_of_mem x h)⟩
        · rw [List.nodup_cons]
          refine ⟨?_, hnd⟩
          intro hx_in
          exact (hmem x hx_in).2 List.mem_cons_self
      · exact ih b seen

private theorem visibleTiles_stripTrailingSentinels (xs : List String) :
    visibleTiles (stripTrailingSentinels xs) = visibleTiles xs := by
  unfold stripTrailingSentinels visibleTiles
  have hrev := List.takeWhile_append_dropWhile (p := fun s => s == rowSentinel) (l := xs.reverse)
  have hxs : xs = (xs.reverse.dropWhile (fun s => s == rowSentinel)).reverse ++
      (xs.reverse.takeWhile (fun s => s == rowSentinel)).reverse := by
    have := congrArg List.reverse hrev
    simp only [List.reverse_append, List.reverse_reverse] at this
    exact this.symm
  have htail_empty :
      ((xs.reverse.takeWhile (fun s => s == rowSentinel)).reverse).filter
        (fun s => s != rowSentinel) = [] := by
    apply List.filter_eq_nil_iff.mpr
    intro y hy
    rw [List.mem_reverse] at hy
    have hpy : (y == rowSentinel) = true :=
      mem_takeWhile_imp_true (p := fun s => s == rowSentinel) hy
    simp [bne, hpy]
  have hfilt : xs.filter (fun s => s != rowSentinel) =
      (xs.reverse.dropWhile (fun s => s == rowSentinel)).reverse.filter (fun s => s != rowSentinel) ++
      ((xs.reverse.takeWhile (fun s => s == rowSentinel)).reverse).filter (fun s => s != rowSentinel) := by
    rw [← List.filter_append, ← hxs]
  rw [hfilt, htail_empty, List.append_nil]

private theorem cleanSavedHiddenAux_spec
    (validIds visible seen xs : List String)
    (hvalid : rowSentinel ∉ validIds) :
    (∀ x ∈ cleanSavedHiddenAux validIds visible seen xs,
      x ∈ validIds ∧ x ∉ visible ∧ x ∉ seen ∧ x ≠ rowSentinel) ∧
    (cleanSavedHiddenAux validIds visible seen xs).Nodup := by
  induction xs generalizing seen with
  | nil => simp [cleanSavedHiddenAux]
  | cons x rest ih =>
    simp only [cleanSavedHiddenAux]
    split
    · rename_i hcond
      simp only [Bool.and_eq_true, Bool.not_eq_true'] at hcond
      have hx_val : x ∈ validIds := List.contains_iff_mem.mp hcond.1.1
      have hx_nvis : x ∉ visible := fun h => by
        have := List.contains_iff_mem.mpr h; rw [hcond.1.2] at this; contradiction
      have hx_nseen : x ∉ seen := fun h => by
        have := List.contains_iff_mem.mpr h; rw [hcond.2] at this; contradiction
      have hx_nsent : x ≠ rowSentinel := fun h => hvalid (h ▸ hx_val)
      have ⟨hmem, hnd⟩ := ih (x :: seen)
      constructor
      · intro y hy
        simp only [List.mem_cons] at hy
        rcases hy with rfl | hy'
        · exact ⟨hx_val, hx_nvis, hx_nseen, hx_nsent⟩
        · have ⟨h1, h2, h3, h4⟩ := hmem y hy'
          exact ⟨h1, h2, fun h => h3 (List.mem_cons_of_mem x h), h4⟩
      · rw [List.nodup_cons]
        exact ⟨fun hx_in => (hmem x hx_in).2.2.1 List.mem_cons_self, hnd⟩
    · exact ih seen

private theorem validLayoutState_initial
    (savedOrder savedHidden tileIds : List String)
    (hvalid : rowSentinel ∉ tileIds) :
    ValidLayoutState tileIds (cleanSavedOrder savedOrder tileIds)
      (cleanSavedHidden savedHidden tileIds (cleanSavedOrder savedOrder tileIds)) := by
  have hcan := canonical_cleanSavedOrder savedOrder tileIds
  have ⟨hvis_mem, hvis_nd⟩ := cleanSavedOrderAux_mem_spec tileIds true [] savedOrder
  have hstrip := visibleTiles_stripTrailingSentinels
    (cleanSavedOrderAux tileIds true [] savedOrder)
  unfold cleanSavedOrder at *
  have ⟨hhid_mem, hhid_nd⟩ := cleanSavedHiddenAux_spec tileIds
    (visibleTiles (stripTrailingSentinels (cleanSavedOrderAux tileIds true [] savedOrder)))
    [] savedHidden hvalid
  refine ⟨hcan, hstrip ▸ hvis_nd, ?_, hhid_nd, ?_, ?_, ?_⟩
  · intro x hx
    rw [hstrip] at hx
    exact (hvis_mem x hx).1
  · intro x hx
    exact (hhid_mem x hx).1
  · intro hmem
    exact (hhid_mem rowSentinel hmem).2.2.2 rfl
  · intro x hxv hxh
    exact (hhid_mem x hxh).2.1 hxv

private theorem validLayoutState_append_order
    {validIds order hidden : List String} {tid : String}
    (hst : ValidLayoutState validIds order hidden)
    (htid_val : tid ∈ validIds) (htid_nsent : tid ≠ rowSentinel)
    (htid_nvis : tid ∉ visibleTiles order) (htid_nhid : tid ∉ hidden) :
    ValidLayoutState validIds (order ++ [tid]) hidden := by
  have htid_bne : (tid != rowSentinel) = true := by simp [bne, beq_false_of_ne htid_nsent]
  have hvis_eq : visibleTiles (order ++ [tid]) = visibleTiles order ++ [tid] := by
    simp [visibleTiles, List.filter_append, htid_bne]
  refine ⟨canonical_append_tile order tid htid_nsent hst.canonical, ?_, ?_,
    hst.hidNodup, hst.hidSub, hst.hidNoSent, ?_⟩
  · rw [hvis_eq, List.nodup_append]
    refine ⟨hst.visNodup, by simp, ?_⟩
    intro a ha b hb heq
    simp at hb; subst hb; subst heq
    exact htid_nvis ha
  · intro x hx
    rw [hvis_eq, List.mem_append, List.mem_singleton] at hx
    rcases hx with h | rfl
    · exact hst.visSub x h
    · exact htid_val
  · intro x hx
    rw [hvis_eq, List.mem_append, List.mem_singleton] at hx
    rcases hx with h | rfl
    · exact hst.disjoint x h
    · exact htid_nhid

private theorem validLayoutState_insertNearAnchor
    {validIds order hidden : List String} {anchor tid : String} {insertBefore : Bool}
    (hst : ValidLayoutState validIds order hidden)
    (hanc_nsent : anchor ≠ rowSentinel)
    (htid_val : tid ∈ validIds) (htid_nsent : tid ≠ rowSentinel)
    (htid_nvis : tid ∉ visibleTiles order) (htid_nhid : tid ∉ hidden) :
    ValidLayoutState validIds (insertNearAnchor anchor tid insertBefore order) hidden := by
  refine ⟨canonical_insertNearAnchor order anchor tid insertBefore hanc_nsent htid_nsent hst.canonical,
    visibleTiles_insertNearAnchor_nodup anchor tid insertBefore order htid_nsent htid_nvis hst.visNodup,
    ?_, hst.hidNodup, hst.hidSub, hst.hidNoSent, ?_⟩
  · intro x hx
    have ⟨hmem, hne⟩ := mem_visibleTiles_iff.mp hx
    rw [mem_insertNearAnchor_iff] at hmem
    rcases hmem with rfl | hmem'
    · exact htid_val
    · exact hst.visSub x (mem_visibleTiles_iff.mpr ⟨hmem', hne⟩)
  · intro x hx
    have ⟨hmem, hne⟩ := mem_visibleTiles_iff.mp hx
    rw [mem_insertNearAnchor_iff] at hmem
    rcases hmem with rfl | hmem'
    · exact htid_nhid
    · exact hst.disjoint x (mem_visibleTiles_iff.mpr ⟨hmem', hne⟩)

private theorem validLayoutState_append_hidden
    {validIds order hidden : List String} {tid : String}
    (hst : ValidLayoutState validIds order hidden)
    (htid_val : tid ∈ validIds) (htid_nsent : tid ≠ rowSentinel)
    (htid_nvis : tid ∉ visibleTiles order) (htid_nhid : tid ∉ hidden) :
    ValidLayoutState validIds order (hidden ++ [tid]) := by
  refine ⟨hst.canonical, hst.visNodup, hst.visSub, ?_, ?_, ?_, ?_⟩
  · rw [List.nodup_append]
    refine ⟨hst.hidNodup, by simp, ?_⟩
    intro a ha b hb heq
    simp at hb; subst hb; subst heq
    exact htid_nhid ha
  · intro x hx
    rw [List.mem_append, List.mem_singleton] at hx
    rcases hx with h | rfl
    · exact hst.hidSub x h
    · exact htid_val
  · intro hmem
    rw [List.mem_append, List.mem_singleton] at hmem
    rcases hmem with h | h
    · exact hst.hidNoSent h
    · exact htid_nsent h.symm
  · intro x hx hmem
    rw [List.mem_append, List.mem_singleton] at hmem
    rcases hmem with h | rfl
    · exact hst.disjoint x hx h
    · exact htid_nvis hx

private theorem findVisibleDependent_ne_sentinel
    (tileIds order : List String) (anchorId dep : String) (b : Bool)
    (hvalid : rowSentinel ∉ tileIds)
    (hfind : findVisibleDependent tileIds order anchorId = some (dep, b)) :
    dep ≠ rowSentinel := by
  unfold findVisibleDependent at hfind
  cases hf : tileIds.find? (fun cand =>
    order.contains cand &&
    match layoutAnchor cand with
    | some (a, _) => a == anchorId
    | none => false) with
  | none =>
    rw [hf] at hfind
    simp at hfind
  | some c =>
    rw [hf] at hfind
    dsimp only at hfind
    cases ha : layoutAnchor c with
    | none =>
      rw [ha] at hfind
      simp at hfind
    | some p =>
      rcases p with ⟨a, b'⟩
      rw [ha] at hfind
      simp only [Option.some.injEq, Prod.mk.injEq] at hfind
      rcases hfind with ⟨rfl, _⟩
      have hmem : c ∈ tileIds := List.mem_of_find?_eq_some hf
      exact fun heq => hvalid (heq ▸ hmem)

private theorem mergePass1_spec
    (allTileIds defaultIds rem order hidden anchoredRev : List String) (changed : Bool)
    (hvalid : rowSentinel ∉ allTileIds)
    (hrem_sub : ∀ x ∈ rem, x ∈ allTileIds)
    (hrem_nodup : rem.Nodup)
    (hanc_sub : ∀ x ∈ anchoredRev, x ∈ allTileIds)
    (hanc_nodup : anchoredRev.Nodup)
    (hanc_disj_rem : ∀ x ∈ anchoredRev, x ∉ rem)
    (hanc_disj_vis : ∀ x ∈ anchoredRev, x ∉ visibleTiles order)
    (hanc_disj_hid : ∀ x ∈ anchoredRev, x ∉ hidden)
    (hst : ValidLayoutState allTileIds order hidden) :
    let (o', h', anc, _) := mergePass1 allTileIds defaultIds rem order hidden anchoredRev changed
    ValidLayoutState allTileIds o' h' ∧
    anc.Nodup ∧
    (∀ x ∈ anc, x ∈ allTileIds ∧ x ∉ visibleTiles o' ∧ x ∉ h') ∧
    (∀ x, x ∈ visibleTiles order → x ∈ visibleTiles o') ∧
    (∀ x, x ∈ hidden → x ∈ h') ∧
    (∀ x ∈ rem, x ∈ visibleTiles o' ∨ x ∈ h' ∨ x ∈ anc) ∧
    (∀ x ∈ anchoredRev, x ∈ anc) := by
  induction rem generalizing order hidden anchoredRev changed with
  | nil =>
    simp only [mergePass1]
    refine ⟨hst, nodup_reverse_of_nodup hanc_nodup, ?_, fun _ h => h, fun _ h => h,
      by simp, fun x hx => List.mem_reverse.mpr hx⟩
    intro x hx
    rw [List.mem_reverse] at hx
    exact ⟨hanc_sub x hx, hanc_disj_vis x hx, hanc_disj_hid x hx⟩
  | cons tid rest ih =>
    rw [List.nodup_cons] at hrem_nodup
    rcases hrem_nodup with ⟨htid_not_rest, hrest_nodup⟩
    have htid_val : tid ∈ allTileIds := hrem_sub tid List.mem_cons_self
    have htid_nsent : tid ≠ rowSentinel := fun h => hvalid (h ▸ htid_val)
    have hrest_sub : ∀ x ∈ rest, x ∈ allTileIds := fun x hx => hrem_sub x (List.mem_cons_of_mem tid hx)
    have hanc_disj_rest : ∀ x ∈ anchoredRev, x ∉ rest :=
      fun x hx hr => hanc_disj_rem x hx (List.mem_cons_of_mem tid hr)
    simp only [mergePass1]
    by_cases hknown : order.contains tid || hidden.contains tid
    · simp only [hknown, ↓reduceIte]
      have hres := ih order hidden anchoredRev changed hrest_sub hrest_nodup
        hanc_sub hanc_nodup hanc_disj_rest hanc_disj_vis hanc_disj_hid hst
      rcases hres with ⟨h1, h2, h3, h4, h5, h6, h7⟩
      refine ⟨h1, h2, h3, h4, h5, ?_, h7⟩
      intro x hx
      rw [List.mem_cons] at hx
      rcases hx with rfl | hx'
      · simp only [Bool.or_eq_true, List.contains_iff_mem] at hknown
        rcases hknown with ho | hh
        · exact Or.inl (h4 x (mem_visibleTiles_iff.mpr ⟨ho, htid_nsent⟩))
        · exact Or.inr (Or.inl (h5 x hh))
      · exact h6 x hx'
    · simp only [hknown, Bool.false_eq_true, ↓reduceIte]
      have htid_norder : tid ∉ order := by
        intro h; apply hknown
        rw [List.contains_iff_mem.mpr h]; rfl
      have htid_nvis : tid ∉ visibleTiles order :=
        fun h => htid_norder (mem_visibleTiles_iff.mp h).1
      have htid_nhid : tid ∉ hidden := by
        intro h; apply hknown
        rw [List.contains_iff_mem.mpr h]; simp
      by_cases hanch : (layoutAnchor tid).isSome
      · simp only [hanch, ↓reduceIte]
        have htid_not_anc : tid ∉ anchoredRev := fun h => hanc_disj_rem tid h List.mem_cons_self
        have hres := ih order hidden (tid :: anchoredRev) changed hrest_sub hrest_nodup
          (fun x hx => by
            rw [List.mem_cons] at hx; rcases hx with rfl | hx'
            · exact htid_val
            · exact hanc_sub x hx')
          (List.nodup_cons.mpr ⟨htid_not_anc, hanc_nodup⟩)
          (fun x hx => by
            rw [List.mem_cons] at hx; rcases hx with rfl | hx'
            · exact htid_not_rest
            · exact hanc_disj_rest x hx')
          (fun x hx => by
            rw [List.mem_cons] at hx; rcases hx with rfl | hx'
            · exact htid_nvis
            · exact hanc_disj_vis x hx')
          (fun x hx => by
            rw [List.mem_cons] at hx; rcases hx with rfl | hx'
            · exact htid_nhid
            · exact hanc_disj_hid x hx')
          hst
        rcases hres with ⟨h1, h2, h3, h4, h5, h6, h7⟩
        refine ⟨h1, h2, h3, h4, h5, ?_, fun x hx => h7 x (List.mem_cons_of_mem tid hx)⟩
        intro x hx
        rw [List.mem_cons] at hx
        rcases hx with rfl | hx'
        · exact Or.inr (Or.inr (h7 x List.mem_cons_self))
        · exact h6 x hx'
      · simp only [hanch, Bool.false_eq_true, ↓reduceIte]
        by_cases hdef : defaultIds.contains tid
        · simp only [hdef, ↓reduceIte]
          -- Case split on findVisibleDependent
          cases hdep : findVisibleDependent allTileIds order tid with
          | none =>
            dsimp only
            have hst' := validLayoutState_append_order hst htid_val htid_nsent htid_nvis htid_nhid
            have hvis_sub' : ∀ x, x ∈ visibleTiles order → x ∈ visibleTiles (order ++ [tid]) := by
              intro x hx
              have ⟨hm, hne⟩ := mem_visibleTiles_iff.mp hx
              exact mem_visibleTiles_iff.mpr ⟨List.mem_append_left _ hm, hne⟩
            have hanc_disj_vis' : ∀ x ∈ anchoredRev, x ∉ visibleTiles (order ++ [tid]) := by
              intro x hx hmem
              have ⟨hm, hne⟩ := mem_visibleTiles_iff.mp hmem
              rw [List.mem_append, List.mem_singleton] at hm
              rcases hm with ho | rfl
              · exact hanc_disj_vis x hx (mem_visibleTiles_iff.mpr ⟨ho, hne⟩)
              · exact hanc_disj_rem x hx List.mem_cons_self
            have hres := ih (order ++ [tid]) hidden anchoredRev true hrest_sub hrest_nodup
              hanc_sub hanc_nodup hanc_disj_rest hanc_disj_vis' hanc_disj_hid hst'
            rcases hres with ⟨h1, h2, h3, h4, h5, h6, h7⟩
            refine ⟨h1, h2, h3, fun x hx => h4 x (hvis_sub' x hx), h5, ?_, h7⟩
            intro x hx
            rw [List.mem_cons] at hx
            rcases hx with rfl | hx'
            · exact Or.inl (h4 x (mem_visibleTiles_iff.mpr ⟨by simp, htid_nsent⟩))
            · exact h6 x hx'
          | some p =>
            rcases p with ⟨dep, depInsertBefore⟩
            dsimp only
            have hdep_nsent := findVisibleDependent_ne_sentinel allTileIds order tid dep depInsertBefore hvalid hdep
            have hst' := validLayoutState_insertNearAnchor (insertBefore := !depInsertBefore)
              hst hdep_nsent htid_val htid_nsent htid_nvis htid_nhid
            have hvis_sub' : ∀ x, x ∈ visibleTiles order →
                x ∈ visibleTiles (insertNearAnchor dep tid (!depInsertBefore) order) := by
              intro x hx
              have ⟨hm, hne⟩ := mem_visibleTiles_iff.mp hx
              exact mem_visibleTiles_iff.mpr ⟨(mem_insertNearAnchor_iff _ _ _ _ _).mpr (Or.inr hm), hne⟩
            have hanc_disj_vis' : ∀ x ∈ anchoredRev,
                x ∉ visibleTiles (insertNearAnchor dep tid (!depInsertBefore) order) := by
              intro x hx hmem
              have ⟨hm, hne⟩ := mem_visibleTiles_iff.mp hmem
              rw [mem_insertNearAnchor_iff] at hm
              rcases hm with rfl | ho
              · exact hanc_disj_rem x hx List.mem_cons_self
              · exact hanc_disj_vis x hx (mem_visibleTiles_iff.mpr ⟨ho, hne⟩)
            have hres := ih (insertNearAnchor dep tid (!depInsertBefore) order) hidden anchoredRev true
              hrest_sub hrest_nodup hanc_sub hanc_nodup hanc_disj_rest hanc_disj_vis' hanc_disj_hid hst'
            rcases hres with ⟨h1, h2, h3, h4, h5, h6, h7⟩
            refine ⟨h1, h2, h3, fun x hx => h4 x (hvis_sub' x hx), h5, ?_, h7⟩
            intro x hx
            rw [List.mem_cons] at hx
            rcases hx with rfl | hx'
            · exact Or.inl (h4 x (mem_visibleTiles_iff.mpr
                ⟨(mem_insertNearAnchor_iff _ _ _ _ _).mpr (Or.inl rfl), htid_nsent⟩))
            · exact h6 x hx'
        · simp only [hdef, Bool.false_eq_true, ↓reduceIte]
          have hst' := validLayoutState_append_hidden hst htid_val htid_nsent htid_nvis htid_nhid
          have hanc_disj_hid' : ∀ x ∈ anchoredRev, x ∉ hidden ++ [tid] := by
            intro x hx hmem
            rw [List.mem_append, List.mem_singleton] at hmem
            rcases hmem with hh | rfl
            · exact hanc_disj_hid x hx hh
            · exact hanc_disj_rem x hx List.mem_cons_self
          have hres := ih order (hidden ++ [tid]) anchoredRev true hrest_sub hrest_nodup
            hanc_sub hanc_nodup hanc_disj_rest hanc_disj_vis hanc_disj_hid' hst'
          rcases hres with ⟨h1, h2, h3, h4, h5, h6, h7⟩
          refine ⟨h1, h2, h3, h4, fun x hx => h5 x (List.mem_append_left _ hx), ?_, h7⟩
          intro x hx
          rw [List.mem_cons] at hx
          rcases hx with rfl | hx'
          · exact Or.inr (Or.inl (h5 x (by simp)))
          · exact h6 x hx'

private theorem gpu_prefix_ne_sentinel (mid suf : String) :
    "gpu_" ++ mid ++ suf ≠ rowSentinel := by
  intro h
  have h1 := congrArg String.toList h
  simp [rowSentinel] at h1

private theorem layoutAnchor_anchor_ne_sentinel
    (tid anchor : String) (b : Bool)
    (h : layoutAnchor tid = some (anchor, b)) :
    anchor ≠ rowSentinel := by
  unfold layoutAnchor at h
  split at h
  · simp at h
    rcases h with ⟨rfl, _⟩
    decide
  · unfold parseGpuAnchor at h
    dsimp only at h
    split at h
    · simp at h
    · split at h
      · split at h
        · simp at h
          rcases h with ⟨rfl, _⟩
          exact gpu_prefix_ne_sentinel _ "_3d"
        · simp at h
      · split at h
        · split at h
          · simp at h
            rcases h with ⟨rfl, _⟩
            exact gpu_prefix_ne_sentinel _ "_vram"
          · simp at h
        · simp at h

private theorem mergePass2_spec
    (validIds anc order hidden : List String) (changed : Bool)
    (hvalid : rowSentinel ∉ validIds)
    (hanc_nodup : anc.Nodup)
    (hanc_spec : ∀ x ∈ anc, x ∈ validIds ∧ x ∉ visibleTiles order ∧ x ∉ hidden ∧ (layoutAnchor x).isSome)
    (hst : ValidLayoutState validIds order hidden) :
    let (o', h', _) := mergePass2 anc order hidden changed
    ValidLayoutState validIds o' h' ∧
    (∀ x, x ∈ visibleTiles order → x ∈ visibleTiles o') ∧
    (∀ x, x ∈ hidden → x ∈ h') ∧
    (∀ x ∈ anc, x ∈ visibleTiles o' ∨ x ∈ h') := by
  induction anc generalizing order hidden changed with
  | nil =>
    simp only [mergePass2]
    exact ⟨hst, fun _ h => h, fun _ h => h, by simp⟩
  | cons tid rest ih =>
    rw [List.nodup_cons] at hanc_nodup
    rcases hanc_nodup with ⟨htid_not_rest, hrest_nodup⟩
    have ⟨htid_val, htid_nvis, htid_nhid, htid_anch⟩ := hanc_spec tid List.mem_cons_self
    have htid_nsent : tid ≠ rowSentinel := fun h => hvalid (h ▸ htid_val)
    simp only [mergePass2]
    cases hla : layoutAnchor tid with
    | none =>
      rw [hla] at htid_anch; simp at htid_anch
    | some p =>
      rcases p with ⟨anchor, insertBefore⟩
      have hanc_nsent := layoutAnchor_anchor_ne_sentinel tid anchor insertBefore hla
      by_cases hcont : order.contains anchor
      · simp only [hcont, ↓reduceIte]
        have hst' := validLayoutState_insertNearAnchor (insertBefore := insertBefore)
          hst hanc_nsent htid_val htid_nsent htid_nvis htid_nhid
        have hvis_sub' : ∀ x, x ∈ visibleTiles order →
            x ∈ visibleTiles (insertNearAnchor anchor tid insertBefore order) := by
          intro x hx
          have ⟨hm, hne⟩ := mem_visibleTiles_iff.mp hx
          exact mem_visibleTiles_iff.mpr ⟨(mem_insertNearAnchor_iff _ _ _ _ _).mpr (Or.inr hm), hne⟩
        have hrest_spec : ∀ x ∈ rest, x ∈ validIds ∧
            x ∉ visibleTiles (insertNearAnchor anchor tid insertBefore order) ∧
            x ∉ hidden ∧ (layoutAnchor x).isSome := by
          intro x hx
          have ⟨h1, h2, h3, h4⟩ := hanc_spec x (List.mem_cons_of_mem tid hx)
          refine ⟨h1, ?_, h3, h4⟩
          intro hmem
          have ⟨hm, hne⟩ := mem_visibleTiles_iff.mp hmem
          rw [mem_insertNearAnchor_iff] at hm
          rcases hm with rfl | ho
          · exact htid_not_rest hx
          · exact h2 (mem_visibleTiles_iff.mpr ⟨ho, hne⟩)
        have ⟨h1, h2, h3, h4⟩ := ih (insertNearAnchor anchor tid insertBefore order) hidden true
          hrest_nodup hrest_spec hst'
        refine ⟨h1, fun x hx => h2 x (hvis_sub' x hx), h3, ?_⟩
        intro x hx
        rw [List.mem_cons] at hx
        rcases hx with rfl | hx'
        · exact Or.inl (h2 x (mem_visibleTiles_iff.mpr
            ⟨(mem_insertNearAnchor_iff _ _ _ _ _).mpr (Or.inl rfl), htid_nsent⟩))
        · exact h4 x hx'
      · simp only [hcont, Bool.false_eq_true, ↓reduceIte]
        have hst' := validLayoutState_append_hidden hst htid_val htid_nsent htid_nvis htid_nhid
        have hrest_spec : ∀ x ∈ rest, x ∈ validIds ∧ x ∉ visibleTiles order ∧
            x ∉ hidden ++ [tid] ∧ (layoutAnchor x).isSome := by
          intro x hx
          have ⟨h1, h2, h3, h4⟩ := hanc_spec x (List.mem_cons_of_mem tid hx)
          refine ⟨h1, h2, ?_, h4⟩
          intro hmem
          rw [List.mem_append, List.mem_singleton] at hmem
          rcases hmem with hh | rfl
          · exact h3 hh
          · exact htid_not_rest hx
        have ⟨h1, h2, h3, h4⟩ := ih order (hidden ++ [tid]) true hrest_nodup hrest_spec hst'
        refine ⟨h1, h2, fun x hx => h3 x (List.mem_append_left _ hx), ?_⟩
        intro x hx
        rw [List.mem_cons] at hx
        rcases hx with rfl | hx'
        · exact Or.inr (h3 x (by simp))
        · exact h4 x hx'

private theorem mergePass1_anchored_isSome
    (allTileIds defaultIds rem order hidden anchoredRev : List String) (changed : Bool)
    (hprev : ∀ x ∈ anchoredRev, (layoutAnchor x).isSome) :
    ∀ x ∈ (mergePass1 allTileIds defaultIds rem order hidden anchoredRev changed).2.2.1,
      (layoutAnchor x).isSome := by
  induction rem generalizing order hidden anchoredRev changed with
  | nil =>
    intro x hx
    simp only [mergePass1, List.mem_reverse] at hx
    exact hprev x hx
  | cons tid rest ih =>
    simp only [mergePass1]
    split
    · exact ih order hidden anchoredRev changed hprev
    · split
      · rename_i hanch
        apply ih order hidden (tid :: anchoredRev) changed
        intro x hx
        rw [List.mem_cons] at hx
        rcases hx with rfl | hx'
        · exact hanch
        · exact hprev x hx'
      · split
        · exact ih _ hidden anchoredRev true hprev
        · exact ih order (hidden ++ [tid]) anchoredRev true hprev

/-- Theorems 2 & 3: `mergeLayoutTiles` produces a valid, canonical, duplicate-free,
    disjoint partition of `tileIds`. -/
theorem mergeLayoutTiles_valid_and_partition
    (savedOrder savedHidden tileIds defaultOrder : List String)
    (hnd : tileIds.Nodup) (hnsent : rowSentinel ∉ tileIds) :
    let (o', h', _) := mergeLayoutTiles savedOrder savedHidden tileIds defaultOrder
    ValidLayoutState tileIds o' h' ∧
    (∀ x, x ∈ tileIds ↔ x ∈ visibleTiles o' ∨ x ∈ h') := by
  unfold mergeLayoutTiles
  dsimp only
  let o0 := cleanSavedOrder savedOrder tileIds
  let h0 := cleanSavedHidden savedHidden tileIds o0
  let c0 := (o0 != savedOrder) || (h0 != savedHidden)
  have hst0 : ValidLayoutState tileIds o0 h0 := validLayoutState_initial savedOrder savedHidden tileIds hnsent
  have hp1 := mergePass1_spec tileIds (visibleTiles defaultOrder) tileIds o0 h0 [] c0
    hnsent (fun _ h => h) hnd (by simp) List.nodup_nil (by simp) (by simp) (by simp) hst0
  have hp1_isSome := mergePass1_anchored_isSome tileIds (visibleTiles defaultOrder) tileIds o0 h0 [] c0 (by simp)
  generalize hm1 : mergePass1 tileIds (visibleTiles defaultOrder) tileIds o0 h0 [] c0 = p1 at hp1 hp1_isSome ⊢
  rcases p1 with ⟨o1, h1, anc, c1⟩
  dsimp only at hp1 hp1_isSome ⊢
  rcases hp1 with ⟨hst1, hanc_nd, hanc_sp, _, _, hcover1, _⟩
  have hanc_full : ∀ x ∈ anc, x ∈ tileIds ∧ x ∉ visibleTiles o1 ∧ x ∉ h1 ∧ (layoutAnchor x).isSome := by
    intro x hx
    have ⟨a, b, c⟩ := hanc_sp x hx
    exact ⟨a, b, c, hp1_isSome x hx⟩
  have hp2 := mergePass2_spec tileIds anc o1 h1 c1 hnsent hanc_nd hanc_full hst1
  generalize hm2 : mergePass2 anc o1 h1 c1 = p2 at hp2 ⊢
  rcases p2 with ⟨o2, h2, c2⟩
  dsimp only at hp2 ⊢
  rcases hp2 with ⟨hst2, hvis_mono, hhid_mono, hanc_cov⟩
  refine ⟨hst2, ?_⟩
  intro x
  constructor
  · intro hx
    rcases hcover1 x hx with hv1 | hh1 | ha1
    · exact Or.inl (hvis_mono x hv1)
    · exact Or.inr (hhid_mono x hh1)
    · exact hanc_cov x ha1
  · rintro (hv2 | hh2)
    · exact hst2.visSub x hv2
    · exact hst2.hidSub x hh2

-- Idempotency of `mergeLayoutTiles`

private theorem cleanSavedOrderAux_idempotent
    (validIds : List String) (b : Bool) (seen xs : List String)
    (hnb : noBadBreaks b xs = true)
    (hnd : (visibleTiles xs).Nodup)
    (hsub : ∀ x ∈ visibleTiles xs, x ∈ validIds)
    (hdisj : ∀ x ∈ visibleTiles xs, x ∉ seen) :
    cleanSavedOrderAux validIds b seen xs = xs := by
  induction xs generalizing b seen with
  | nil => rfl
  | cons x rest ih =>
    simp only [cleanSavedOrderAux]
    by_cases hx : x = rowSentinel
    · subst hx
      simp only [beq_self_eq_true, ↓reduceIte]
      simp only [noBadBreaks, beq_self_eq_true, ↓reduceIte] at hnb
      cases b with
      | true => simp at hnb
      | false =>
        simp only [Bool.not_false, ↓reduceIte]
        simp only [visibleTiles, List.filter_cons, bne_self_eq_false, Bool.false_eq_true,
          ↓reduceIte] at hnd hsub hdisj
        rw [ih true seen hnb hnd hsub hdisj]
    · have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
      have hx_bne : (x != rowSentinel) = true := by simp [bne, hx_beq]
      simp only [hx_beq, Bool.false_eq_true, ↓reduceIte]
      simp only [noBadBreaks, hx_beq, Bool.false_eq_true, ↓reduceIte] at hnb
      simp only [visibleTiles, List.filter_cons, hx_bne, ↓reduceIte] at hnd hsub hdisj
      rw [List.nodup_cons] at hnd
      have hx_val : validIds.contains x = true := List.contains_iff_mem.mpr (hsub x List.mem_cons_self)
      have hx_nseen : seen.contains x = false := by
        cases hc : seen.contains x with
        | false => rfl
        | true => exact absurd (List.contains_iff_mem.mp hc) (hdisj x List.mem_cons_self)
      simp only [hx_val, hx_nseen, Bool.not_false, Bool.and_self, ↓reduceIte]
      have hsub' : ∀ y ∈ visibleTiles rest, y ∈ validIds :=
        fun y hy => hsub y (List.mem_cons_of_mem x hy)
      have hdisj' : ∀ y ∈ visibleTiles rest, y ∉ x :: seen := by
        intro y hy hmem
        rw [List.mem_cons] at hmem
        rcases hmem with rfl | hs
        · exact hnd.1 hy
        · exact hdisj y (List.mem_cons_of_mem x hy) hs
      rw [ih false (x :: seen) hnb hnd.2 hsub' hdisj']

private theorem isCanonicalAux_imp_noBadBreaks_and_last
    (b : Bool) (xs : List String) (hcan : isCanonicalAux b xs = true) :
    noBadBreaks b xs = true ∧ (∀ hne : xs ≠ [], xs.getLast hne ≠ rowSentinel) := by
  induction xs generalizing b with
  | nil => exact ⟨rfl, fun h => absurd rfl h⟩
  | cons x rest ih =>
    cases rest with
    | nil =>
      simp only [isCanonicalAux] at hcan
      have hx : x ≠ rowSentinel := by intro h; subst h; simp at hcan
      have hx_beq : (x == rowSentinel) = false := beq_false_of_ne hx
      exact ⟨by simp [noBadBreaks, hx_beq], fun _ => by simpa using hx⟩
    | cons y ys =>
      simp only [isCanonicalAux] at hcan
      simp only [noBadBreaks]
      split at hcan
      · rename_i hxs
        simp only [hxs, ↓reduceIte]
        split at hcan
        · contradiction
        · rename_i hb
          simp only [hb, Bool.false_eq_true, ↓reduceIte]
          have ⟨h1, h2⟩ := ih true hcan
          exact ⟨h1, fun _ => by simpa [List.getLast_cons] using h2 (by simp)⟩
      · rename_i hxs
        simp only [hxs, Bool.false_eq_true, ↓reduceIte]
        have ⟨h1, h2⟩ := ih false hcan
        exact ⟨h1, fun _ => by simpa [List.getLast_cons] using h2 (by simp)⟩

private theorem stripTrailingSentinels_of_last_ne
    (xs : List String) (hlast : ∀ hne : xs ≠ [], xs.getLast hne ≠ rowSentinel) :
    stripTrailingSentinels xs = xs := by
  unfold stripTrailingSentinels
  cases hrev : xs.reverse with
  | nil =>
    have : xs = [] := List.reverse_eq_nil_iff.mp hrev
    subst this; rfl
  | cons z zs =>
    have hne : xs ≠ [] := by
      intro h; subst h; simp at hrev
    have hz : z = xs.getLast hne := by
      have hrev_eq : xs = (z :: zs).reverse := by
        rw [← hrev, List.reverse_reverse]
      have hgl : (z :: zs).reverse.getLast (by simp) = z := by
        simp [List.reverse_cons]
      have : xs.getLast hne = (z :: zs).reverse.getLast (by simp) := by
        cases hrev_eq; rfl
      rw [this, hgl]
    have hz_ne : z ≠ rowSentinel := hz ▸ hlast hne
    have hz_beq : (z == rowSentinel) = false := beq_false_of_ne hz_ne
    simp only [List.dropWhile_cons, hz_beq, Bool.false_eq_true, ↓reduceIte]
    rw [← hrev, List.reverse_reverse]

private theorem cleanSavedOrder_idempotent
    (order validIds : List String)
    (hcan : CanonicalOrder order)
    (hnd : (visibleTiles order).Nodup)
    (hsub : ∀ x ∈ visibleTiles order, x ∈ validIds) :
    cleanSavedOrder order validIds = order := by
  unfold cleanSavedOrder
  cases order with
  | nil => rfl
  | cons x xs =>
    unfold CanonicalOrder isCanonicalOrder at hcan
    have ⟨hnb, hlast⟩ := isCanonicalAux_imp_noBadBreaks_and_last true (x :: xs) hcan
    rw [cleanSavedOrderAux_idempotent validIds true [] (x :: xs) hnb hnd hsub (by simp)]
    exact stripTrailingSentinels_of_last_ne (x :: xs) hlast

private theorem cleanSavedHiddenAux_idempotent
    (validIds visible seen xs : List String)
    (hnd : xs.Nodup)
    (hsub : ∀ x ∈ xs, x ∈ validIds)
    (hdisj_vis : ∀ x ∈ xs, x ∉ visible)
    (hdisj_seen : ∀ x ∈ xs, x ∉ seen) :
    cleanSavedHiddenAux validIds visible seen xs = xs := by
  induction xs generalizing seen with
  | nil => rfl
  | cons x rest ih =>
    rw [List.nodup_cons] at hnd
    have hx_val : validIds.contains x = true := List.contains_iff_mem.mpr (hsub x List.mem_cons_self)
    have hx_nvis : visible.contains x = false := by
      cases hc : visible.contains x with
      | false => rfl
      | true => exact absurd (List.contains_iff_mem.mp hc) (hdisj_vis x List.mem_cons_self)
    have hx_nseen : seen.contains x = false := by
      cases hc : seen.contains x with
      | false => rfl
      | true => exact absurd (List.contains_iff_mem.mp hc) (hdisj_seen x List.mem_cons_self)
    simp only [cleanSavedHiddenAux, hx_val, hx_nvis, hx_nseen, Bool.not_false, Bool.and_self, ↓reduceIte]
    rw [ih (x :: seen) hnd.2
      (fun y hy => hsub y (List.mem_cons_of_mem x hy))
      (fun y hy => hdisj_vis y (List.mem_cons_of_mem x hy))
      (fun y hy hmem => by
        rw [List.mem_cons] at hmem
        rcases hmem with rfl | hs
        · exact hnd.1 hy
        · exact hdisj_seen y (List.mem_cons_of_mem x hy) hs)]

private theorem mergePass1_noop
    (allTileIds defaultIds rem order hidden : List String)
    (hcover : ∀ x ∈ rem, x ∈ order ∨ x ∈ hidden) :
    mergePass1 allTileIds defaultIds rem order hidden [] false = (order, hidden, [], false) := by
  induction rem with
  | nil => rfl
  | cons tid rest ih =>
    have hknown : (order.contains tid || hidden.contains tid) = true := by
      rcases hcover tid List.mem_cons_self with ho | hh
      · rw [List.contains_iff_mem.mpr ho]; rfl
      · rw [List.contains_iff_mem.mpr hh]; simp
    simp only [mergePass1, hknown, ↓reduceIte]
    exact ih (fun x hx => hcover x (List.mem_cons_of_mem tid hx))

/-- Theorem 4: Idempotency of `mergeLayoutTiles` (matches docstring claim in `system_tricorder.py:4640`). -/
theorem mergeLayoutTiles_idempotent
    (savedOrder savedHidden tileIds defaultOrder : List String)
    (hnd : tileIds.Nodup) (hnsent : rowSentinel ∉ tileIds) :
    let (o', h', _) := mergeLayoutTiles savedOrder savedHidden tileIds defaultOrder
    mergeLayoutTiles o' h' tileIds defaultOrder = (o', h', false) := by
  have hvp := mergeLayoutTiles_valid_and_partition savedOrder savedHidden tileIds defaultOrder hnd hnsent
  generalize hm : mergeLayoutTiles savedOrder savedHidden tileIds defaultOrder = res at hvp ⊢
  rcases res with ⟨o', h', c⟩
  dsimp only at hvp ⊢
  rcases hvp with ⟨hst, hpart⟩
  unfold mergeLayoutTiles
  dsimp only
  have ho_eq : cleanSavedOrder o' tileIds = o' :=
    cleanSavedOrder_idempotent o' tileIds hst.canonical hst.visNodup hst.visSub
  have hh_eq : cleanSavedHidden h' tileIds o' = h' := by
    unfold cleanSavedHidden
    exact cleanSavedHiddenAux_idempotent tileIds (visibleTiles o') [] h'
      hst.hidNodup hst.hidSub (fun x hx hxv => hst.disjoint x hxv hx) (by simp)
  rw [ho_eq, hh_eq]
  simp only [bne_self_eq_false, Bool.or_self]
  have hcover : ∀ x ∈ tileIds, x ∈ o' ∨ x ∈ h' := by
    intro x hx
    rcases (hpart x).mp hx with hv | hh
    · exact Or.inl (mem_visibleTiles_iff.mp hv).1
    · exact Or.inr hh
  rw [mergePass1_noop tileIds (visibleTiles defaultOrder) tileIds o' h' hcover]
  rfl

-- ── 4. Dormant hardware layout preservation ───────────────────────────────────

/-- Splice `seg` into `rows` immediately before or after the first row containing `anchor`. -/
def spliceInRow (anchor : String) (seg : List String) (after : Bool) :
    List String → Option (List String)
  | [] => none
  | x :: xs =>
    if x == anchor then
      some (if after then x :: (seg ++ xs) else seg ++ (x :: xs))
    else
      match spliceInRow anchor seg after xs with
      | none => none
      | some xs' => some (x :: xs')

def spliceInRows (anchor : String) (seg : List String) (after : Bool) :
    List (List String) → Option (List (List String))
  | [] => none
  | r :: rs =>
    match spliceInRow anchor seg after r with
    | some r' => some (r' :: rs)
    | none =>
      match spliceInRows anchor seg after rs with
      | some rs' => some (r :: rs')
      | none => none

def lookupAssoc (k : String) : List (String × String) → Option String
  | [] => none
  | (k', v) :: rest => if k == k' then some v else lookupAssoc k rest

def setAssoc (k v : String) : List (String × String) → List (String × String)
  | [] => [(k, v)]
  | (k', v') :: rest => if k == k' then (k, v) :: rest else (k', v') :: setAssoc k v rest

/-- Extract maximal contiguous dormant runs from one stored row, tracking `prevActive`/`nextActive`. -/
def dormantSegmentsAux
    (activeIds activeVisible : List String)
    (prevActive : Option String) :
    List String → List (Option String × List String × Option String)
  | [] => []
  | x :: xs =>
    if activeIds.contains x then
      let prev' := if activeVisible.contains x then some x else prevActive
      dormantSegmentsAux activeIds activeVisible prev' xs
    else
      let seg := x :: xs.takeWhile (fun t => !activeIds.contains t)
      let rest := xs.dropWhile (fun t => !activeIds.contains t)
      let nextAct := rest.find? (fun t => activeVisible.contains t)
      (prevActive, seg, nextAct) :: dormantSegmentsAux activeIds activeVisible prevActive rest
termination_by l => l.length
decreasing_by
  · simp_wf
  · simp_wf
    have hle : (xs.dropWhile (fun t => !activeIds.contains t)).length ≤ xs.length :=
      length_dropWhile_le _ _
    change (xs.dropWhile (fun t => !activeIds.contains t)).length < xs.length + 1
    omega

def dedupNewDormant (seen : List String) : List String → (List String × List String)
  | [] => ([], seen)
  | t :: ts =>
    if t != rowSentinel && !seen.contains t then
      let (seg', seen') := dedupNewDormant (t :: seen) ts
      (t :: seg', seen')
    else
      dedupNewDormant seen ts

def placeDormantSegments
    (segments : List (Option String × List String × Option String))
    (rows orphanRows : List (List String))
    (dormantSeen : List String)
    (afterTails : List (String × String)) :
    (List (List String) × List (List String) × List String × List (String × String)) :=
  match segments with
  | [] => (rows, orphanRows, dormantSeen, afterTails)
  | (prevOpt, rawSeg, nextOpt) :: rest =>
    let (seg, dormantSeen') := dedupNewDormant dormantSeen rawSeg
    if seg.isEmpty then
      placeDormantSegments rest rows orphanRows dormantSeen' afterTails
    else
      let placedPrev :=
        match prevOpt with
        | none => none
        | some p =>
          let anchor := (lookupAssoc p afterTails).getD p
          match spliceInRows anchor seg true rows with
          | none => none
          | some rows' =>
            let tail := seg.getLastD p
            some (rows', setAssoc p tail afterTails)
      match placedPrev with
      | some (rows', afterTails') =>
        placeDormantSegments rest rows' orphanRows dormantSeen' afterTails'
      | none =>
        let placedNext :=
          match nextOpt with
          | none => none
          | some f => spliceInRows f seg false rows
        match placedNext with
        | some rows' =>
          placeDormantSegments rest rows' orphanRows dormantSeen' afterTails
        | none =>
          placeDormantSegments rest rows (orphanRows ++ [seg]) dormantSeen' afterTails

def processStoredRows
    (activeIds activeVisible : List String)
    (storedRows rows orphanRows : List (List String))
    (dormantSeen : List String)
    (afterTails : List (String × String)) :
    (List (List String) × List (List String) × List String) :=
  match storedRows with
  | [] => (rows, orphanRows, dormantSeen)
  | srow :: rest =>
    let segs := dormantSegmentsAux activeIds activeVisible none srow
    let (rows', orphanRows', dormantSeen', afterTails') :=
      placeDormantSegments segs rows orphanRows dormantSeen afterTails
    processStoredRows activeIds activeVisible rest rows' orphanRows' dormantSeen' afterTails'

def dedupOrderPreserving (seen : List String) : List String → List String
  | [] => []
  | x :: xs =>
    if seen.contains x then dedupOrderPreserving seen xs
    else x :: dedupOrderPreserving (x :: seen) xs

def preserveDormantLayout
    (activeOrder activeHidden storedOrder storedHidden activeIds : List String) :
    (List String × List String) :=
  let rows0 := tileRows activeOrder
  let activeVisible := visibleTiles activeOrder
  let storedRows := tileRows storedOrder
  let (rowsFinal, orphanRows, dormantVisible) :=
    processStoredRows activeIds activeVisible storedRows rows0 [] [] []
  let persistedOrder := flattenTileRows (rowsFinal ++ orphanRows)
  let activeHidClean := dedupOrderPreserving [] (activeHidden.filter (fun t => activeIds.contains t))
  let dormantHid := dedupOrderPreserving activeHidClean
    (storedHidden.filter (fun t =>
      !activeIds.contains t && !dormantVisible.contains t))
  (persistedOrder, activeHidClean ++ dormantHid)

-- ── Proofs for preserveDormantLayout ──────────────────────────────────────────

private theorem dedupNewDormant_spec (seen raw : List String) :
    let (seg, seen') := dedupNewDormant seen raw
    seg.Nodup ∧
    (rowSentinel ∉ seg) ∧
    (∀ x ∈ seg, x ∈ raw ∧ x ∉ seen) ∧
    (∀ x, x ∈ seen' ↔ x ∈ seg ∨ x ∈ seen) ∧
    (∀ x ∈ raw, x ≠ rowSentinel → x ∈ seg ∨ x ∈ seen) := by
  induction raw generalizing seen with
  | nil =>
    simp [dedupNewDormant]
  | cons t ts ih =>
    simp only [dedupNewDormant]
    by_cases hcond : t != rowSentinel && !seen.contains t
    · simp only [hcond, ↓reduceIte]
      simp only [Bool.and_eq_true, Bool.not_eq_true'] at hcond
      have ht_nsent : t ≠ rowSentinel := by
        intro h; subst h; simp at hcond
      have ht_nseen : t ∉ seen := fun h => by
        have := List.contains_iff_mem.mpr h; rw [hcond.2] at this; contradiction
      have hih := ih (t :: seen)
      generalize hd : dedupNewDormant (t :: seen) ts = res at hih ⊢
      rcases res with ⟨seg', seen'⟩
      rcases hih with ⟨hnd, hns, hmem, hseen', hcov⟩
      refine ⟨?_, ?_, ?_, ?_, ?_⟩
      · rw [List.nodup_cons]
        exact ⟨fun ht_in => (hmem t ht_in).2 List.mem_cons_self, hnd⟩
      · intro h
        rw [List.mem_cons] at h
        rcases h with heq | h'
        · exact ht_nsent heq.symm
        · exact hns h'
      · intro x hx
        rw [List.mem_cons] at hx
        rcases hx with rfl | hx'
        · exact ⟨List.mem_cons_self, ht_nseen⟩
        · have ⟨h1, h2⟩ := hmem x hx'
          exact ⟨List.mem_cons_of_mem t h1, fun hs => h2 (List.mem_cons_of_mem t hs)⟩
      · intro x
        rw [hseen']
        simp only [List.mem_cons]
        grind
      · intro x hx hne
        rw [List.mem_cons] at hx
        rcases hx with rfl | hx'
        · exact Or.inl List.mem_cons_self
        · rcases hcov x hx' hne with h1 | h2
          · exact Or.inl (List.mem_cons_of_mem t h1)
          · rw [List.mem_cons] at h2
            rcases h2 with rfl | hs
            · exact Or.inl List.mem_cons_self
            · exact Or.inr hs
    · simp only [hcond, Bool.false_eq_true, ↓reduceIte]
      have hih := ih seen
      generalize hd : dedupNewDormant seen ts = res at hih ⊢
      rcases res with ⟨seg', seen'⟩
      rcases hih with ⟨hnd, hns, hmem, hseen', hcov⟩
      refine ⟨hnd, hns, fun x hx => ⟨List.mem_cons_of_mem t (hmem x hx).1, (hmem x hx).2⟩,
        hseen', ?_⟩
      intro x hx hne
      rw [List.mem_cons] at hx
      rcases hx with rfl | hx'
      · have ht_bne : (x != rowSentinel) = true := by simp [bne, beq_false_of_ne hne]
        have hct : seen.contains x = true := by
          cases hc : seen.contains x with
          | true => rfl
          | false =>
            have htrue : (x != rowSentinel && !seen.contains x) = true := by
              rw [ht_bne, hc]; rfl
            exact absurd htrue hcond
        exact Or.inr (List.contains_iff_mem.mp hct)
      · exact hcov x hx' hne

private theorem spliceInRow_mem_iff
    (anchor : String) (seg : List String) (after : Bool) (r r' : List String)
    (h : spliceInRow anchor seg after r = some r') :
    ∀ x, x ∈ r' ↔ x ∈ seg ∨ x ∈ r := by
  induction r generalizing r' with
  | nil => simp [spliceInRow] at h
  | cons y ys ih =>
    simp only [spliceInRow] at h
    split at h
    · simp at h; subst h
      intro x
      cases after <;> simp [List.mem_cons, List.mem_append] <;> grind
    · cases hs : spliceInRow anchor seg after ys with
      | none => simp [hs] at h
      | some ys' =>
        simp [hs] at h; subst h
        intro x
        simp only [List.mem_cons, ih ys' hs x]
        grind

private theorem spliceInRow_nodup
    (anchor : String) (seg : List String) (after : Bool) (r r' : List String)
    (h : spliceInRow anchor seg after r = some r')
    (hr : r.Nodup) (hseg : seg.Nodup) (hdisj : ∀ x ∈ seg, x ∉ r) :
    r'.Nodup := by
  induction r generalizing r' with
  | nil => simp [spliceInRow] at h
  | cons y ys ih =>
    rw [List.nodup_cons] at hr
    rcases hr with ⟨hy_not_ys, hys_nd⟩
    simp only [spliceInRow] at h
    split at h
    · simp at h; subst h
      cases after with
      | true =>
        simp only [↓reduceIte]
        rw [List.nodup_cons, List.nodup_append]
        refine ⟨?_, hseg, hys_nd, fun a ha b hb heq => hdisj a ha (List.mem_cons_of_mem y (heq ▸ hb))⟩
        intro hy_in
        rw [List.mem_append] at hy_in
        rcases hy_in with hs | hys
        · exact hdisj y hs List.mem_cons_self
        · exact hy_not_ys hys
      | false =>
        simp only [Bool.false_eq_true, ↓reduceIte]
        rw [List.nodup_append]
        exact ⟨hseg, List.nodup_cons.mpr ⟨hy_not_ys, hys_nd⟩, fun a ha b hb heq => hdisj a ha (heq ▸ hb)⟩
    · cases hs : spliceInRow anchor seg after ys with
      | none => simp [hs] at h
      | some ys' =>
        simp [hs] at h; subst h
        rw [List.nodup_cons]
        refine ⟨?_, ih ys' hs hys_nd (fun x hx hy => hdisj x hx (List.mem_cons_of_mem y hy))⟩
        intro hy_in
        rw [spliceInRow_mem_iff anchor seg after ys ys' hs] at hy_in
        rcases hy_in with hseg_in | hys_in
        · exact hdisj y hseg_in List.mem_cons_self
        · exact hy_not_ys hys_in

private theorem spliceInRows_flatten_mem
    (anchor : String) (seg : List String) (after : Bool) (rows rows' : List (List String))
    (h : spliceInRows anchor seg after rows = some rows') :
    ∀ x, x ∈ rows'.flatten ↔ x ∈ seg ∨ x ∈ rows.flatten := by
  induction rows generalizing rows' with
  | nil => simp [spliceInRows] at h
  | cons r rs ih =>
    simp only [spliceInRows] at h
    cases hr : spliceInRow anchor seg after r with
    | some r' =>
      simp [hr] at h; subst h
      intro x
      simp only [List.flatten_cons, List.mem_append, spliceInRow_mem_iff anchor seg after r r' hr]
      grind
    | none =>
      simp only [hr] at h
      cases hrs : spliceInRows anchor seg after rs with
      | none => simp [hrs] at h
      | some rs' =>
        simp [hrs] at h; subst h
        intro x
        simp only [List.flatten_cons, List.mem_append, ih rs' hrs x]
        grind

private theorem spliceInRows_no_sentinel
    (anchor : String) (seg : List String) (after : Bool) (rows rows' : List (List String))
    (h : spliceInRows anchor seg after rows = some rows')
    (hseg : rowSentinel ∉ seg)
    (hrows : ∀ r ∈ rows, rowSentinel ∉ r) :
    ∀ r ∈ rows', rowSentinel ∉ r := by
  induction rows generalizing rows' with
  | nil => simp [spliceInRows] at h
  | cons r rs ih =>
    simp only [spliceInRows] at h
    cases hr : spliceInRow anchor seg after r with
    | some r' =>
      simp [hr] at h; subst h
      intro q hq
      rw [List.mem_cons] at hq
      rcases hq with rfl | hqs
      · intro hmem
        rw [spliceInRow_mem_iff anchor seg after r q hr] at hmem
        rcases hmem with h1 | h2
        · exact hseg h1
        · exact hrows r List.mem_cons_self h2
      · exact hrows q (List.mem_cons_of_mem r hqs)
    | none =>
      simp only [hr] at h
      cases hrs : spliceInRows anchor seg after rs with
      | none => simp [hrs] at h
      | some rs' =>
        simp [hrs] at h; subst h
        intro q hq
        rw [List.mem_cons] at hq
        rcases hq with rfl | hqs
        · exact hrows q List.mem_cons_self
        · exact ih rs' hrs (fun p hp => hrows p (List.mem_cons_of_mem r hp)) q hqs

private theorem spliceInRows_flatten_nodup
    (anchor : String) (seg : List String) (after : Bool) (rows rows' : List (List String))
    (h : spliceInRows anchor seg after rows = some rows')
    (hrows : rows.flatten.Nodup) (hseg : seg.Nodup)
    (hdisj : ∀ x ∈ seg, x ∉ rows.flatten) :
    rows'.flatten.Nodup := by
  induction rows generalizing rows' with
  | nil => simp [spliceInRows] at h
  | cons r rs ih =>
    rw [List.flatten_cons, List.nodup_append] at hrows
    rcases hrows with ⟨hr_nd, hrs_nd, hrrs_disj⟩
    simp only [spliceInRows] at h
    cases hr : spliceInRow anchor seg after r with
    | some r' =>
      simp [hr] at h; subst h
      rw [List.flatten_cons, List.nodup_append]
      refine ⟨spliceInRow_nodup anchor seg after r r' hr hr_nd hseg
        (fun x hx hr_in => hdisj x hx (by simp [hr_in])), hrs_nd, ?_⟩
      intro a ha b hb heq; subst heq
      rw [spliceInRow_mem_iff anchor seg after r r' hr] at ha
      rcases ha with hs | hr_in
      · exact hdisj a hs (by simp [hb])
      · exact hrrs_disj a hr_in a hb rfl
    | none =>
      simp only [hr] at h
      cases hrs : spliceInRows anchor seg after rs with
      | none => simp [hrs] at h
      | some rs' =>
        simp [hrs] at h; subst h
        rw [List.flatten_cons, List.nodup_append]
        refine ⟨hr_nd, ih rs' hrs hrs_nd (fun x hx hmem => hdisj x hx (by simp [hmem])), ?_⟩
        intro a ha b hb heq; subst heq
        rw [spliceInRows_flatten_mem anchor seg after rs rs' hrs] at hb
        rcases hb with hs | hrs_in
        · exact hdisj a hs (by simp [ha])
        · exact hrrs_disj a ha a hrs_in rfl

private theorem dormantSegmentsAux_mem_spec
    (activeIds activeVisible : List String) (prev : Option String) (row : List String) :
    (∀ seg ∈ dormantSegmentsAux activeIds activeVisible prev row,
      ∀ x ∈ seg.2.1, x ∈ row ∧ x ∉ activeIds) ∧
    (∀ x ∈ row, x ∉ activeIds →
      ∃ seg ∈ dormantSegmentsAux activeIds activeVisible prev row, x ∈ seg.2.1) := by
  -- Strong induction on `row.length`
  suffices h : ∀ n (row : List String) (prev : Option String), row.length ≤ n →
      (∀ seg ∈ dormantSegmentsAux activeIds activeVisible prev row,
        ∀ x ∈ seg.2.1, x ∈ row ∧ x ∉ activeIds) ∧
      (∀ x ∈ row, x ∉ activeIds →
        ∃ seg ∈ dormantSegmentsAux activeIds activeVisible prev row, x ∈ seg.2.1) by
    exact h row.length row prev (Nat.le_refl _)
  intro n
  induction n with
  | zero =>
    intro row prev hlen
    cases row with
    | nil => simp [dormantSegmentsAux]
    | cons _ _ => simp at hlen
  | succ m ih =>
    intro row prev hlen
    cases row with
    | nil => simp [dormantSegmentsAux]
    | cons x xs =>
      simp only [List.length_cons] at hlen
      rw [dormantSegmentsAux]
      by_cases hact : activeIds.contains x
      · simp only [hact, ↓reduceIte]
        have ⟨h1, h2⟩ := ih xs (if activeVisible.contains x then some x else prev) (by omega)
        constructor
        · intro seg hseg y hy
          have ⟨hm, hna⟩ := h1 seg hseg y hy
          exact ⟨List.mem_cons_of_mem x hm, hna⟩
        · intro y hy yna
          rw [List.mem_cons] at hy
          rcases hy with rfl | hy'
          · exact absurd (List.contains_iff_mem.mp hact) yna
          · exact h2 y hy' yna
      · simp only [hact, Bool.false_eq_true, ↓reduceIte]
        have hx_nact : x ∉ activeIds := fun h => hact (List.contains_iff_mem.mpr h)
        have hrest_le : (xs.dropWhile (fun t => !activeIds.contains t)).length ≤ m := by
          have := length_dropWhile_le (fun t => !activeIds.contains t) xs
          omega
        have ⟨h1, h2⟩ := ih (xs.dropWhile (fun t => !activeIds.contains t)) prev hrest_le
        have hsplit := List.takeWhile_append_dropWhile (p := fun t => !activeIds.contains t) (l := xs)
        constructor
        · intro seg hseg y hy
          rw [List.mem_cons] at hseg
          rcases hseg with rfl | hseg'
          · dsimp only at hy
            rw [List.mem_cons] at hy
            rcases hy with rfl | htw
            · exact ⟨List.mem_cons_self, hx_nact⟩
            · have hy_xs : y ∈ xs := by
                rw [← hsplit]; exact List.mem_append_left _ htw
              have hp := mem_takeWhile_imp_true htw
              simp only [Bool.not_eq_true'] at hp
              exact ⟨List.mem_cons_of_mem x hy_xs, fun h => by
                have := List.contains_iff_mem.mpr h; rw [hp] at this; contradiction⟩
          · have ⟨hm, hna⟩ := h1 seg hseg' y hy
            have hy_xs : y ∈ xs := by
              rw [← hsplit]; exact List.mem_append_right _ hm
            exact ⟨List.mem_cons_of_mem x hy_xs, hna⟩
        · intro y hy yna
          rw [List.mem_cons] at hy
          rcases hy with rfl | hy'
          · exact ⟨_, List.mem_cons_self, List.mem_cons_self⟩
          · rw [← hsplit, List.mem_append] at hy'
            rcases hy' with htw | hdw
            · exact ⟨_, List.mem_cons_self, List.mem_cons_of_mem x htw⟩
            · have ⟨seg, hseg, hys⟩ := h2 y hdw yna
              exact ⟨seg, List.mem_cons_of_mem _ hseg, hys⟩

private def PlacePost
    (activeIds : List String)
    (segs : List (Option String × List String × Option String))
    (rows0 orph0 : List (List String)) (seen0 : List String)
    (res : List (List String) × List (List String) × List String × List (String × String)) : Prop :=
  let (rows', orph', seen', _) := res
  (∀ r ∈ rows', rowSentinel ∉ r) ∧
  (∀ r ∈ orph', rowSentinel ∉ r) ∧
  (rows' ++ orph').flatten.Nodup ∧
  (∀ x, x ∈ seen' → x ∉ activeIds) ∧
  (∀ x, x ∉ activeIds → (x ∈ (rows' ++ orph').flatten ↔ x ∈ seen')) ∧
  (∀ x, x ∈ (rows0 ++ orph0).flatten → x ∈ (rows' ++ orph').flatten) ∧
  (∀ x, x ∈ activeIds → (x ∈ (rows' ++ orph').flatten ↔ x ∈ (rows0 ++ orph0).flatten)) ∧
  (∀ x, x ∈ seen0 → x ∈ seen') ∧
  (∀ s ∈ segs, ∀ x ∈ s.2.1, x ≠ rowSentinel → x ∈ seen')

private theorem placeDormantSegments_inv
    (activeIds : List String)
    (segs : List (Option String × List String × Option String))
    (rows orphanRows : List (List String))
    (dormantSeen : List String)
    (afterTails : List (String × String))
    (hsegs_dormant : ∀ s ∈ segs, ∀ x ∈ s.2.1, x ∉ activeIds)
    (hno_rows : ∀ r ∈ rows, rowSentinel ∉ r)
    (hno_orph : ∀ r ∈ orphanRows, rowSentinel ∉ r)
    (hnd_flat : (rows ++ orphanRows).flatten.Nodup)
    (hseen_disj : ∀ x, x ∈ dormantSeen → x ∉ activeIds)
    (hseen_eq : ∀ x, x ∉ activeIds → (x ∈ (rows ++ orphanRows).flatten ↔ x ∈ dormantSeen)) :
    PlacePost activeIds segs rows orphanRows dormantSeen
      (placeDormantSegments segs rows orphanRows dormantSeen afterTails) := by
  induction segs generalizing rows orphanRows dormantSeen afterTails with
  | nil =>
    dsimp only [placeDormantSegments, PlacePost]
    exact ⟨hno_rows, hno_orph, hnd_flat, hseen_disj, hseen_eq,
      fun _ h => h, fun _ _ => Iff.rfl, fun _ h => h, by simp⟩
  | cons seg rest ih =>
    rcases seg with ⟨prevOpt, rawSeg, nextOpt⟩
    have hraw_dormant : ∀ x ∈ rawSeg, x ∉ activeIds := hsegs_dormant _ List.mem_cons_self
    have hrest_dormant : ∀ s ∈ rest, ∀ x ∈ s.2.1, x ∉ activeIds :=
      fun s hs => hsegs_dormant s (List.mem_cons_of_mem _ hs)
    have hdd := dedupNewDormant_spec dormantSeen rawSeg
    dsimp only [placeDormantSegments]
    generalize hdd_eq : dedupNewDormant dormantSeen rawSeg = ddRes at hdd ⊢
    rcases ddRes with ⟨cleanSeg, seen1⟩
    dsimp only at hdd ⊢
    rcases hdd with ⟨hseg_nd, hseg_nsent, hseg_mem, hseen1_iff, hraw_cov⟩
    have hseg_dormant : ∀ x ∈ cleanSeg, x ∉ activeIds :=
      fun x hx => hraw_dormant x (hseg_mem x hx).1
    have hseen1_disj : ∀ x, x ∈ seen1 → x ∉ activeIds := by
      intro x hx
      rw [hseen1_iff] at hx
      rcases hx with h1 | h2
      · exact hseg_dormant x h1
      · exact hseen_disj x h2
    have hseg_disj_flat : ∀ x ∈ cleanSeg, x ∉ (rows ++ orphanRows).flatten := by
      intro x hx hmem
      have hna := hseg_dormant x hx
      have hsin := (hseen_eq x hna).mp hmem
      exact (hseg_mem x hx).2 hsin
    by_cases hempty : cleanSeg.isEmpty
    · simp only [hempty, ↓reduceIte]
      have hcs_nil : cleanSeg = [] := by
        cases cleanSeg with | nil => rfl | cons _ _ => simp at hempty
      have hseen1_eq : ∀ x, x ∈ seen1 ↔ x ∈ dormantSeen := by
        intro x; rw [hseen1_iff, hcs_nil]; simp
      have hseen_eq' : ∀ x, x ∉ activeIds → (x ∈ (rows ++ orphanRows).flatten ↔ x ∈ seen1) :=
        fun x hna => (hseen_eq x hna).trans (hseen1_eq x).symm
      have hres := ih rows orphanRows seen1 afterTails hrest_dormant hno_rows hno_orph
        hnd_flat hseen1_disj hseen_eq'
      dsimp only [PlacePost] at hres ⊢
      rcases hres with ⟨a1, a2, a3, a4, a5, a6, a7, a8, a9⟩
      refine ⟨a1, a2, a3, a4, a5, a6, a7, fun x hx => a8 x ((hseen1_eq x).mpr hx), ?_⟩
      intro s hs x hx hne
      rw [List.mem_cons] at hs
      rcases hs with rfl | hs'
      · exact a8 x ((hseen1_iff x).mpr (hraw_cov x hx hne))
      · exact a9 s hs' x hx hne
    · simp only [hempty, Bool.false_eq_true, ↓reduceIte]
      have hstep : ∀ rows1 orph1 at1,
          (∀ r ∈ rows1, rowSentinel ∉ r) →
          (∀ r ∈ orph1, rowSentinel ∉ r) →
          (rows1 ++ orph1).flatten.Nodup →
          (∀ x, x ∈ (rows1 ++ orph1).flatten ↔ x ∈ cleanSeg ∨ x ∈ (rows ++ orphanRows).flatten) →
          PlacePost activeIds ((prevOpt, rawSeg, nextOpt) :: rest) rows orphanRows dormantSeen
            (placeDormantSegments rest rows1 orph1 seen1 at1) := by
        intro rows1 orph1 at1 hno1_r hno1_o hnd1 hmem1
        have hseen_eq1 : ∀ x, x ∉ activeIds → (x ∈ (rows1 ++ orph1).flatten ↔ x ∈ seen1) := by
          intro x hna
          rw [hmem1, hseen1_iff, hseen_eq x hna]
        have hres := ih rows1 orph1 seen1 at1 hrest_dormant hno1_r hno1_o hnd1 hseen1_disj hseen_eq1
        dsimp only [PlacePost] at hres ⊢
        rcases hres with ⟨a1, a2, a3, a4, a5, a6, a7, a8, a9⟩
        refine ⟨a1, a2, a3, a4, a5,
          fun x hx => a6 x ((hmem1 x).mpr (Or.inr hx)),
          ?_,
          fun x hx => a8 x ((hseen1_iff x).mpr (Or.inr hx)),
          ?_⟩
        · intro x hx
          rw [a7 x hx, hmem1 x]
          constructor
          · rintro (hs | hf)
            · exact absurd hx (hseg_dormant x hs)
            · exact hf
          · exact Or.inr
        · intro s hs x hx hne
          rw [List.mem_cons] at hs
          rcases hs with rfl | hs'
          · exact a8 x ((hseen1_iff x).mpr (hraw_cov x hx hne))
          · exact a9 s hs' x hx hne
      have hstep_splice : ∀ anchor after rows1 at1,
          spliceInRows anchor cleanSeg after rows = some rows1 →
          PlacePost activeIds ((prevOpt, rawSeg, nextOpt) :: rest) rows orphanRows dormantSeen
            (placeDormantSegments rest rows1 orphanRows seen1 at1) := by
        intro anchor after rows1 at1 hsp
        apply hstep rows1 orphanRows at1
        · exact spliceInRows_no_sentinel anchor cleanSeg after rows rows1 hsp hseg_nsent hno_rows
        · exact hno_orph
        · rw [List.flatten_append, List.nodup_append] at hnd_flat ⊢
          rcases hnd_flat with ⟨hr_nd, ho_nd, hro_disj⟩
          refine ⟨spliceInRows_flatten_nodup anchor cleanSeg after rows rows1 hsp hr_nd hseg_nd
            (fun x hx hr_in => hseg_disj_flat x hx (by simp [hr_in])), ho_nd, ?_⟩
          intro a ha b hb heq; subst heq
          rw [spliceInRows_flatten_mem anchor cleanSeg after rows rows1 hsp] at ha
          rcases ha with hs | hr_in
          · exact hseg_disj_flat a hs (by simp [hb])
          · exact hro_disj a hr_in a hb rfl
        · intro x
          rw [List.flatten_append, List.flatten_append, List.mem_append, List.mem_append,
            spliceInRows_flatten_mem anchor cleanSeg after rows rows1 hsp]
          constructor
          · rintro ((hs | hr) | ho)
            · exact Or.inl hs
            · exact Or.inr (Or.inl hr)
            · exact Or.inr (Or.inr ho)
          · rintro (hs | (hr | ho))
            · exact Or.inl (Or.inl hs)
            · exact Or.inl (Or.inr hr)
            · exact Or.inr ho
      have hstep_orphan :
          PlacePost activeIds ((prevOpt, rawSeg, nextOpt) :: rest) rows orphanRows dormantSeen
            (placeDormantSegments rest rows (orphanRows ++ [cleanSeg]) seen1 afterTails) := by
        apply hstep rows (orphanRows ++ [cleanSeg]) afterTails
        · exact hno_rows
        · intro r hr
          rw [List.mem_append, List.mem_singleton] at hr
          rcases hr with h | rfl
          · exact hno_orph r h
          · exact hseg_nsent
        · have hfl : (rows ++ (orphanRows ++ [cleanSeg])).flatten =
              (rows ++ orphanRows).flatten ++ cleanSeg := by
            simp [List.append_assoc, List.flatten_append]
          rw [hfl, List.nodup_append]
          exact ⟨hnd_flat, hseg_nd, fun a ha b hb heq => hseg_disj_flat b hb (heq ▸ ha)⟩
        · intro x
          have hfl : (rows ++ (orphanRows ++ [cleanSeg])).flatten =
              (rows ++ orphanRows).flatten ++ cleanSeg := by
            simp [List.append_assoc, List.flatten_append]
          rw [hfl, List.mem_append]
          exact ⟨fun h => h.symm, fun h => h.symm⟩
      cases prevOpt with
      | none =>
        dsimp only
        cases nextOpt with
        | none => exact hstep_orphan
        | some f =>
          dsimp only
          cases hsp : spliceInRows f cleanSeg false rows with
          | none => exact hstep_orphan
          | some rows1 => exact hstep_splice f false rows1 afterTails hsp
      | some p =>
        dsimp only
        cases hsp1 : spliceInRows ((lookupAssoc p afterTails).getD p) cleanSeg true rows with
        | some rows1 =>
          exact hstep_splice _ true rows1 (setAssoc p (cleanSeg.getLastD p) afterTails) hsp1
        | none =>
          dsimp only
          cases nextOpt with
          | none => exact hstep_orphan
          | some f =>
            dsimp only
            cases hsp2 : spliceInRows f cleanSeg false rows with
            | none => exact hstep_orphan
            | some rows1 => exact hstep_splice f false rows1 afterTails hsp2

private theorem processStoredRows_inv
    (activeIds activeVisible : List String)
    (storedRows rows orphanRows : List (List String))
    (dormantSeen : List String)
    (afterTails : List (String × String))
    (hno_rows : ∀ r ∈ rows, rowSentinel ∉ r)
    (hno_orph : ∀ r ∈ orphanRows, rowSentinel ∉ r)
    (hnd_flat : (rows ++ orphanRows).flatten.Nodup)
    (hseen_disj : ∀ x, x ∈ dormantSeen → x ∉ activeIds)
    (hseen_eq : ∀ x, x ∉ activeIds → (x ∈ (rows ++ orphanRows).flatten ↔ x ∈ dormantSeen)) :
    let (rows', orph', seen') :=
      processStoredRows activeIds activeVisible storedRows rows orphanRows dormantSeen afterTails
    (∀ r ∈ rows', rowSentinel ∉ r) ∧
    (∀ r ∈ orph', rowSentinel ∉ r) ∧
    (rows' ++ orph').flatten.Nodup ∧
    (∀ x, x ∈ seen' → x ∉ activeIds) ∧
    (∀ x, x ∉ activeIds → (x ∈ (rows' ++ orph').flatten ↔ x ∈ seen')) ∧
    (∀ x, x ∈ activeIds → (x ∈ (rows' ++ orph').flatten ↔ x ∈ (rows ++ orphanRows).flatten)) ∧
    (∀ x, x ∈ dormantSeen → x ∈ seen') ∧
    (∀ r ∈ storedRows, ∀ x ∈ r, x ∉ activeIds → x ≠ rowSentinel → x ∈ seen') := by
  induction storedRows generalizing rows orphanRows dormantSeen afterTails with
  | nil =>
    dsimp only [processStoredRows]
    exact ⟨hno_rows, hno_orph, hnd_flat, hseen_disj, hseen_eq,
      fun _ _ => Iff.rfl, fun _ h => h, by simp⟩
  | cons srow rest ih =>
    dsimp only [processStoredRows]
    have ⟨hseg_m, hseg_c⟩ := dormantSegmentsAux_mem_spec activeIds activeVisible none srow
    have hpl := placeDormantSegments_inv activeIds
      (dormantSegmentsAux activeIds activeVisible none srow)
      rows orphanRows dormantSeen afterTails
      (fun s hs x hx => (hseg_m s hs x hx).2)
      hno_rows hno_orph hnd_flat hseen_disj hseen_eq
    generalize hpl_eq : placeDormantSegments
      (dormantSegmentsAux activeIds activeVisible none srow)
      rows orphanRows dormantSeen afterTails = plRes at hpl ⊢
    rcases plRes with ⟨rows1, orph1, seen1, at1⟩
    dsimp only [PlacePost] at hpl
    dsimp only
    rcases hpl with ⟨b1, b2, b3, b4, b5, _, b7, b8, b9⟩
    have hih := ih rows1 orph1 seen1 at1 b1 b2 b3 b4 b5
    generalize hih_eq : processStoredRows activeIds activeVisible rest rows1 orph1 seen1 at1 = finRes at hih ⊢
    rcases finRes with ⟨rowsF, orphF, seenF⟩
    dsimp only at hih ⊢
    rcases hih with ⟨c1, c2, c3, c4, c5, c6, c7, c8⟩
    refine ⟨c1, c2, c3, c4, c5,
      fun x hx => (c6 x hx).trans (b7 x hx),
      fun x hx => c7 x (b8 x hx),
      ?_⟩
    intro r hr x hx hna hne
    rw [List.mem_cons] at hr
    rcases hr with rfl | hr'
    · have ⟨seg, hseg, hxs⟩ := hseg_c x hx hna
      exact c7 x (b9 seg hseg x hxs hne)
    · exact c8 r hr' x hx hna hne

private theorem dedupOrderPreserving_spec (seen xs : List String) :
    (dedupOrderPreserving seen xs).Nodup ∧
    (∀ x, x ∈ dedupOrderPreserving seen xs ↔ x ∈ xs ∧ x ∉ seen) := by
  induction xs generalizing seen with
  | nil => simp [dedupOrderPreserving]
  | cons y ys ih =>
    simp only [dedupOrderPreserving]
    by_cases hys : seen.contains y
    · simp only [hys, ↓reduceIte]
      have hy_in : y ∈ seen := List.contains_iff_mem.mp hys
      have ⟨hnd, hmem⟩ := ih seen
      refine ⟨hnd, ?_⟩
      intro x
      rw [hmem]
      constructor
      · rintro ⟨hx, hns⟩; exact ⟨List.mem_cons_of_mem y hx, hns⟩
      · rintro ⟨hx_cons, hns⟩
        rw [List.mem_cons] at hx_cons
        rcases hx_cons with rfl | hx
        · exact absurd hy_in hns
        · exact ⟨hx, hns⟩
    · simp only [hys, Bool.false_eq_true, ↓reduceIte]
      have hy_nin : y ∉ seen := fun h => hys (List.contains_iff_mem.mpr h)
      have ⟨hnd, hmem⟩ := ih (y :: seen)
      constructor
      · rw [List.nodup_cons]
        exact ⟨fun hy => (hmem y).mp hy |>.2 List.mem_cons_self, hnd⟩
      · intro x
        rw [List.mem_cons, hmem]
        constructor
        · rintro (rfl | ⟨hx, hns⟩)
          · exact ⟨List.mem_cons_self, hy_nin⟩
          · exact ⟨List.mem_cons_of_mem y hx, fun hs => hns (List.mem_cons_of_mem y hs)⟩
        · rintro ⟨hx_cons, hns⟩
          rw [List.mem_cons] at hx_cons
          rcases hx_cons with rfl | hx
          · exact Or.inl rfl
          · by_cases hxy : x = y
            · exact Or.inl hxy
            · exact Or.inr ⟨hx, fun h => by
                rw [List.mem_cons] at h
                rcases h with heq | hs
                · exact hxy heq
                · exact hns hs⟩

/-- Theorem 6: `preserveDormantLayout` preserves canonical normal form, active tiles without
    duplication, and every dormant tile from `storedOrder ∪ storedHidden` exactly once
    (disjointly across persisted visible order and persisted hidden list). -/
theorem preserveDormantLayout_spec
    (activeOrder activeHidden storedOrder storedHidden activeIds : List String)
    (hact_nd : (visibleTiles activeOrder).Nodup)
    (hact_sub : ∀ x ∈ visibleTiles activeOrder, x ∈ activeIds)
    (hact_disj : ∀ x ∈ visibleTiles activeOrder, x ∉ activeHidden) :
    let (pOrder, pHidden) :=
      preserveDormantLayout activeOrder activeHidden storedOrder storedHidden activeIds
    CanonicalOrder pOrder ∧
    (visibleTiles pOrder).Nodup ∧
    pHidden.Nodup ∧
    (∀ x, x ∈ visibleTiles pOrder → x ∉ pHidden) ∧
    (∀ x ∈ activeIds, (x ∈ visibleTiles pOrder ↔ x ∈ visibleTiles activeOrder)) ∧
    (∀ x, x ∉ activeIds → x ≠ rowSentinel →
      (x ∈ visibleTiles storedOrder ∨ x ∈ storedHidden) →
      (x ∈ visibleTiles pOrder ∨ x ∈ pHidden)) := by
  unfold preserveDormantLayout
  dsimp only
  have hno_rows0 := tileRows_no_sentinel activeOrder
  have hflat0 : (tileRows activeOrder ++ []).flatten = visibleTiles activeOrder := by
    simp [flatten_tileRows_eq_visibleTiles]
  have hnd0 : (tileRows activeOrder ++ []).flatten.Nodup := hflat0 ▸ hact_nd
  have hseen_eq0 : ∀ x, x ∉ activeIds → (x ∈ (tileRows activeOrder ++ []).flatten ↔ x ∈ ([] : List String)) := by
    intro x hna
    rw [hflat0]
    simp only [List.not_mem_nil, iff_false]
    intro hv; exact hna (hact_sub x hv)
  have hproc := processStoredRows_inv activeIds (visibleTiles activeOrder)
    (tileRows storedOrder) (tileRows activeOrder) [] [] []
    hno_rows0 (by simp) hnd0 (by simp) hseen_eq0
  generalize hpeq : processStoredRows activeIds (visibleTiles activeOrder)
    (tileRows storedOrder) (tileRows activeOrder) [] [] [] = procRes at hproc ⊢
  rcases procRes with ⟨rowsFinal, orphanRows, dormantVisible⟩
  dsimp only at hproc ⊢
  rcases hproc with ⟨hno_rf, hno_or, hnd_all, hdv_dorm, hdv_iff, hact_iff, _, hstored_cov⟩
  have hno_comb : ∀ r ∈ rowsFinal ++ orphanRows, rowSentinel ∉ r := by
    intro r hr
    rw [List.mem_append] at hr
    rcases hr with h1 | h2
    · exact hno_rf r h1
    · exact hno_or r h2
  have hvis_pOrder : visibleTiles (flattenTileRows (rowsFinal ++ orphanRows)) =
      (rowsFinal ++ orphanRows).flatten :=
    visibleTiles_flattenTileRows (rowsFinal ++ orphanRows) hno_comb
  have ⟨hah_nd, hah_mem⟩ := dedupOrderPreserving_spec []
    (activeHidden.filter (fun t => activeIds.contains t))
  have ⟨hdh_nd, hdh_mem⟩ := dedupOrderPreserving_spec
    (dedupOrderPreserving [] (activeHidden.filter (fun t => activeIds.contains t)))
    (storedHidden.filter (fun t => !activeIds.contains t && !dormantVisible.contains t))
  refine ⟨canonical_flattenTileRows _ hno_comb, hvis_pOrder ▸ hnd_all, ?_, ?_, ?_, ?_⟩
  · -- pHidden.Nodup
    rw [List.nodup_append]
    refine ⟨hah_nd, hdh_nd, ?_⟩
    intro a ha b hb heq; subst heq
    exact ((hdh_mem a).mp hb).2 ha
  · -- Disjointness
    intro x hx hhid
    rw [hvis_pOrder] at hx
    rw [List.mem_append] at hhid
    rcases hhid with hah | hdh
    · have hf := ((hah_mem x).mp hah).1
      rw [List.mem_filter] at hf
      have hx_act : x ∈ activeIds := List.contains_iff_mem.mp hf.2
      have hx_vis0 : x ∈ visibleTiles activeOrder := by
        rw [← hflat0]
        exact (hact_iff x hx_act).mp hx
      exact hact_disj x hx_vis0 hf.1
    · have hf := ((hdh_mem x).mp hdh).1
      rw [List.mem_filter] at hf
      simp only [Bool.and_eq_true, Bool.not_eq_true'] at hf
      have hx_nact : x ∉ activeIds := fun h => by
        have := List.contains_iff_mem.mpr h; rw [hf.2.1] at this; contradiction
      have hx_ndv : x ∉ dormantVisible := fun h => by
        have := List.contains_iff_mem.mpr h; rw [hf.2.2] at this; contradiction
      exact hx_ndv ((hdv_iff x hx_nact).mp hx)
  · -- Active tiles preserved exactly
    intro x hx
    rw [hvis_pOrder, hact_iff x hx, hflat0]
  · -- Dormant tiles from storedOrder ∪ storedHidden preserved
    intro x hna hne hstored
    rw [hvis_pOrder]
    rcases hstored with hso | hsh
    · have hfl : x ∈ (tileRows storedOrder).flatten := by
        rw [flatten_tileRows_eq_visibleTiles]; exact hso
      rcases List.mem_flatten.mp hfl with ⟨r, hr, hxr⟩
      have hx_dv := hstored_cov r hr x hxr hna hne
      exact Or.inl ((hdv_iff x hna).mpr hx_dv)
    · by_cases hdv : x ∈ dormantVisible
      · exact Or.inl ((hdv_iff x hna).mpr hdv)
      · right
        apply List.mem_append_right
        apply (hdh_mem x).mpr
        constructor
        · rw [List.mem_filter]
          refine ⟨hsh, ?_⟩
          have h1 : activeIds.contains x = false := by
            cases hc : activeIds.contains x with
            | false => rfl
            | true => exact absurd (List.contains_iff_mem.mp hc) hna
          have h2 : dormantVisible.contains x = false := by
            cases hc : dormantVisible.contains x with
            | false => rfl
            | true => exact absurd (List.contains_iff_mem.mp hc) hdv
          rw [h1, h2]; rfl
        · intro hah
          have hf := ((hah_mem x).mp hah).1
          rw [List.mem_filter] at hf
          exact hna (List.contains_iff_mem.mp hf.2)

end Tricorder.Layout
