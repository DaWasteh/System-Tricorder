/-!
# Formal Specification & Proofs for System Tricorder Power Aggregation

Models:
- `_cpu_package_power_from_pdh` (`system_tricorder.py`, lines 1362–1418)
- `_linux_rapl_energy_delta` (`system_tricorder.py`, lines 995–1008)

Uses exact integer arithmetic (`Nat`/`Int` in milliwatts / microjoules) to avoid opaque
floating-point axioms while matching Python's domain selection and wrap-around semantics.
-/

namespace Tricorder.Power

private def isAsciiDigit (c : Char) : Bool := '0' ≤ c && c ≤ '9'
private def isAsciiLowerAlphaNum (c : Char) : Bool :=
  ('a' ≤ c && c ≤ 'z') || ('0' ≤ c && c ≤ '9')

private def lowerAsciiChar (c : Char) : Char :=
  if 'A' ≤ c && c ≤ 'Z' then Char.ofNat (c.toNat + 32) else c

private def isWsChar (c : Char) : Bool :=
  c == ' ' || c == '\t' || c == '\n' || c == '\r'

private def stripWsChars (cs : List Char) : List Char :=
  ((cs.dropWhile isWsChar).reverse.dropWhile isWsChar).reverse

/-- Strip a trailing `#<digits>` PDH instance suffix (matches `re.sub(r"#\d+$", "", s)`). -/
private def stripHashDigitsSuffix (cs : List Char) : List Char :=
  let rev := cs.reverse
  let digits := rev.takeWhile isAsciiDigit
  let afterDigits := rev.dropWhile isAsciiDigit
  if !digits.isEmpty then
    match afterDigits with
    | '#' :: rest => rest.reverse
    | _ => cs
  else
    cs

/-- Collapse runs of non-alphanumeric chars into single `'_'` and trim outer `'_'`. -/
private def collapseNonAlnum (prevUnderscore : Bool) : List Char → List Char
  | [] => []
  | c :: rest =>
    if isAsciiLowerAlphaNum c then
      c :: collapseNonAlnum false rest
    else if prevUnderscore then
      collapseNonAlnum true rest
    else
      '_' :: collapseNonAlnum true rest

private def trimTrailingUnderscores (cs : List Char) : List Char :=
  (cs.reverse.dropWhile (fun c => c == '_')).reverse

def normalisePdhName (raw : String) : String :=
  let stripped := stripWsChars raw.toList
  let lowered := stripped.map lowerAsciiChar
  let noSuffix := stripHashDigitsSuffix lowered
  let collapsed := collapseNonAlnum true noSuffix
  String.ofList (trimTrailingUnderscores collapsed)

-- ── Disjoint priority tiers ───────────────────────────────────────────────────

inductive PowerTier where
  | raplPkg
  | currentSocketPower
  | currentSocketEnergy
  | socketPower
  | cpuPackagePower
  | cpuPower
  | apuPower
  | apuEnergy
  | raplCore
  | ignored
  deriving DecidableEq, Repr

/-- Exact tier priority order (highest to lowest; `.ignored` never selected). -/
def activeTiers : List PowerTier :=
  [ .raplPkg
  , .currentSocketPower
  , .currentSocketEnergy
  , .socketPower
  , .cpuPackagePower
  , .cpuPower
  , .apuPower
  , .apuEnergy
  , .raplCore
  ]

def packageTiers : List PowerTier :=
  [ .raplPkg
  , .currentSocketPower
  , .currentSocketEnergy
  , .socketPower
  , .cpuPackagePower
  , .cpuPower
  , .apuPower
  , .apuEnergy
  ]

private def isRaplPkgName (s : String) : Bool :=
  if s.startsWith "rapl_package" && s.endsWith "_pkg" then
    let mid := ((s.drop 12).dropEnd 4).toString
    !mid.isEmpty && mid.all isAsciiDigit
  else false

private def isRaplCoreName (s : String) : Bool :=
  if s.startsWith "rapl_package" && s.endsWith "_core" then
    let mid := ((s.drop 12).dropEnd 5).toString
    match mid.splitOn "_core" with
    | [pkgDigits, coreDigits] =>
      !pkgDigits.isEmpty && pkgDigits.all isAsciiDigit &&
      !coreDigits.isEmpty && coreDigits.all isAsciiDigit
    | _ => false
  else false

def classifyPdhName (normName : String) : PowerTier :=
  if isRaplPkgName normName then .raplPkg
  else if normName == "current_socket_power" then .currentSocketPower
  else if normName == "current_socket_energy" then .currentSocketEnergy
  else if normName == "socket_power" then .socketPower
  else if normName == "cpu_package_power" then .cpuPackagePower
  else if normName == "cpu_power" then .cpuPower
  else if normName == "apu_power" then .apuPower
  else if normName == "apu_energy" then .apuEnergy
  else if isRaplCoreName normName then .raplCore
  else .ignored

-- ── Positive-value filtering & duplicate-name deduplication ───────────────────

def dedupPositiveRowsAux (seen : List String) : List (String × Int) → List (String × Nat)
  | [] => []
  | (rawName, rawVal) :: rest =>
    if rawVal ≤ 0 then
      dedupPositiveRowsAux seen rest
    else
      let name := normalisePdhName rawName
      if name.isEmpty || seen.contains name then
        dedupPositiveRowsAux seen rest
      else
        (name, rawVal.toNat) :: dedupPositiveRowsAux (name :: seen) rest

def dedupPositiveRows (rows : List (String × Int)) : List (String × Nat) :=
  dedupPositiveRowsAux [] rows

def tierValues (tier : PowerTier) (normRows : List (String × Nat)) : List Nat :=
  (normRows.filter (fun p => classifyPdhName p.1 == tier)).map Prod.snd

def sumNat : List Nat → Nat
  | [] => 0
  | x :: xs => x + sumNat xs

def selectFirstNonEmptyTier (normRows : List (String × Nat)) : List PowerTier → Option Nat
  | [] => none
  | t :: ts =>
    let vals := tierValues t normRows
    if vals.isEmpty then
      selectFirstNonEmptyTier normRows ts
    else
      some (sumNat vals)

/-- Total CPU package power in milliwatts (matches `_cpu_package_power_from_pdh` × 1000). -/
def cpuPackagePowerMw (rows : List (String × Int)) : Option Nat :=
  selectFirstNonEmptyTier (dedupPositiveRows rows) activeTiers

-- ── Theorems for PDH CPU Power Aggregation ────────────────────────────────────

private theorem dedupPositiveRowsAux_spec (seen : List String) (rows : List (String × Int)) :
    (∀ p ∈ dedupPositiveRowsAux seen rows, p.1 ∉ seen ∧ 0 < p.2 ∧ !p.1.isEmpty) ∧
    ((dedupPositiveRowsAux seen rows).map Prod.fst).Nodup := by
  induction rows generalizing seen with
  | nil => simp [dedupPositiveRowsAux]
  | cons r rest ih =>
    rcases r with ⟨rawName, rawVal⟩
    simp only [dedupPositiveRowsAux]
    by_cases hle : rawVal ≤ 0
    · simp only [hle, ↓reduceIte]
      exact ih seen
    · simp only [hle, ↓reduceIte]
      generalize hnm : normalisePdhName rawName = nm
      by_cases hskip : nm.isEmpty || seen.contains nm
      · simp only [hskip, ↓reduceIte]
        exact ih seen
      · simp only [hskip, Bool.false_eq_true, ↓reduceIte]
        have hne_empty : nm.isEmpty = false := by
          cases h : nm.isEmpty with
          | false => rfl
          | true =>
            exfalso; apply hskip; rw [h]; rfl
        have hnot_seen : nm ∉ seen := by
          intro hmem
          apply hskip
          rw [List.contains_iff_mem.mpr hmem]; simp
        have hpos : 0 < rawVal.toNat := by omega
        have ⟨hmem, hnd⟩ := ih (nm :: seen)
        constructor
        · intro p hp
          rw [List.mem_cons] at hp
          rcases hp with rfl | hp'
          · exact ⟨hnot_seen, hpos, by simp [hne_empty]⟩
          · have ⟨h1, h2, h3⟩ := hmem p hp'
            exact ⟨fun hs => h1 (List.mem_cons_of_mem _ hs), h2, h3⟩
        · rw [List.map_cons, List.nodup_cons]
          refine ⟨?_, hnd⟩
          intro hdup
          rcases List.mem_map.mp hdup with ⟨q, hq, heq⟩
          exact (hmem q hq).1 (heq ▸ List.mem_cons_self)

/-- Theorem P1 (No Duplicate Counter Counting): Every normalized PDH counter name appears
    at most once in the deduplicated positive row list. -/
theorem dedupPositiveRows_nodup_names (rows : List (String × Int)) :
    ((dedupPositiveRows rows).map Prod.fst).Nodup :=
  (dedupPositiveRowsAux_spec [] rows).2

private theorem selectFirstNonEmptyTier_spec
    (normRows : List (String × Nat)) (tiers : List PowerTier) (total : Nat)
    (h : selectFirstNonEmptyTier normRows tiers = some total) :
    ∃ t ∈ tiers,
      tierValues t normRows ≠ [] ∧
      total = sumNat (tierValues t normRows) ∧
      (∀ t_hi, tiers.idxOf t_hi < tiers.idxOf t → tierValues t_hi normRows = []) := by
  induction tiers with
  | nil => simp [selectFirstNonEmptyTier] at h
  | cons t ts ih =>
    simp only [selectFirstNonEmptyTier] at h
    by_cases hemp : (tierValues t normRows).isEmpty
    · simp only [hemp, ↓reduceIte] at h
      have ht_nil : tierValues t normRows = [] := by
        cases hv : tierValues t normRows with
        | nil => rfl
        | cons _ _ => simp [hv] at hemp
      have ⟨t', ht'_mem, ht'_ne, htot, hhi⟩ := ih h
      have ht'_ne_t : t' ≠ t := by
        intro heq; subst heq; exact ht'_ne ht_nil
      refine ⟨t', List.mem_cons_of_mem t ht'_mem, ht'_ne, htot, ?_⟩
      intro t_hi hlt
      by_cases hthi_t : t_hi = t
      · subst hthi_t; exact ht_nil
      · have hidx1 : (t :: ts).idxOf t_hi = ts.idxOf t_hi + 1 := by
          simp [List.idxOf_cons, beq_false_of_ne (Ne.symm hthi_t)]
        have hidx2 : (t :: ts).idxOf t' = ts.idxOf t' + 1 := by
          simp [List.idxOf_cons, beq_false_of_ne (Ne.symm ht'_ne_t)]
        rw [hidx1, hidx2] at hlt
        exact hhi t_hi (by omega)
    · simp only [hemp, Bool.false_eq_true, ↓reduceIte, Option.some.injEq] at h
      have ht_ne : tierValues t normRows ≠ [] := by
        intro hnil; rw [hnil] at hemp; simp at hemp
      refine ⟨t, List.mem_cons_self, ht_ne, h.symm, ?_⟩
      intro t_hi hlt
      simp only [List.idxOf_cons_self] at hlt
      omega

/-- Theorem P2 (Single-Tier Exclusivity / No Cross-Domain Double Counting):
    Whenever `cpuPackagePowerMw` returns `some total`, `total` comes exclusively from ONE
    active tier `t ≠ .ignored` (and every higher-priority tier is empty). -/
theorem cpuPackagePowerMw_single_tier (rows : List (String × Int)) (total : Nat)
    (h : cpuPackagePowerMw rows = some total) :
    ∃ t ∈ activeTiers,
      t ≠ .ignored ∧
      tierValues t (dedupPositiveRows rows) ≠ [] ∧
      total = sumNat (tierValues t (dedupPositiveRows rows)) ∧
      (∀ t_hi, activeTiers.idxOf t_hi < activeTiers.idxOf t →
        tierValues t_hi (dedupPositiveRows rows) = []) := by
  have ⟨t, ht_mem, ht_ne, htot, hhi⟩ := selectFirstNonEmptyTier_spec (dedupPositiveRows rows) activeTiers total h
  have ht_not_ign : t ≠ .ignored := by
    intro heq; subst heq; simp [activeTiers] at ht_mem
  exact ⟨t, ht_mem, ht_not_ign, ht_ne, htot, hhi⟩

/-- Theorem P3 (Core Fallback Exclusivity): If ANY whole-package tier has a positive row,
    per-core (`RAPL_PackageN_CoreM_CORE`) rows are never added to the result. -/
theorem cpuPackagePowerMw_core_never_added_to_package
    (rows : List (String × Int))
    (hpkg : ∃ t ∈ packageTiers, tierValues t (dedupPositiveRows rows) ≠ []) :
    ∃ t ∈ packageTiers,
      cpuPackagePowerMw rows = some (sumNat (tierValues t (dedupPositiveRows rows))) := by
  have hact : activeTiers = packageTiers ++ [.raplCore] := rfl
  unfold cpuPackagePowerMw
  rw [hact]
  suffices ∀ (ts : List PowerTier),
      (∃ t ∈ ts, tierValues t (dedupPositiveRows rows) ≠ []) →
      ∃ t ∈ ts, selectFirstNonEmptyTier (dedupPositiveRows rows) (ts ++ [.raplCore]) =
        some (sumNat (tierValues t (dedupPositiveRows rows))) by
    exact this packageTiers hpkg
  intro ts hex
  induction ts with
  | nil =>
    rcases hex with ⟨_, hmem, _⟩
    simp at hmem
  | cons t rest ih =>
    simp only [List.cons_append, selectFirstNonEmptyTier]
    by_cases hemp : (tierValues t (dedupPositiveRows rows)).isEmpty
    · simp only [hemp, ↓reduceIte]
      have ht_nil : tierValues t (dedupPositiveRows rows) = [] := by
        cases hv : tierValues t (dedupPositiveRows rows) with
        | nil => rfl
        | cons _ _ => simp [hv] at hemp
      have hex' : ∃ t' ∈ rest, tierValues t' (dedupPositiveRows rows) ≠ [] := by
        rcases hex with ⟨t', ht'm, ht'ne⟩
        rw [List.mem_cons] at ht'm
        rcases ht'm with rfl | hrest
        · exact absurd ht_nil ht'ne
        · exact ⟨t', hrest, ht'ne⟩
      have ⟨t', ht'm, hres⟩ := ih hex'
      exact ⟨t', List.mem_cons_of_mem t ht'm, hres⟩
    · simp only [hemp, Bool.false_eq_true, ↓reduceIte]
      exact ⟨t, List.mem_cons_self, rfl⟩

/-- Theorem P4 (PP0/DRAM Subdomains Ignored): Rows classified as `.ignored` (including
    `rapl_package0_pp0` and `rapl_package0_dram`) contribute to zero active tiers. -/
theorem tierValues_ignore_ignored_row
    (t : PowerTier) (ht : t ≠ .ignored) (name : String) (v : Nat) (rest : List (String × Nat))
    (hign : classifyPdhName name = .ignored) :
    tierValues t ((name, v) :: rest) = tierValues t rest := by
  unfold tierValues
  have hneq : (classifyPdhName name == t) = false := by
    rw [hign]
    exact beq_false_of_ne ht.symm
  simp [hneq]

/-- Theorem P5 (Non-Positive Rows Filtered): If all rows have `val ≤ 0`, result is `none`. -/
theorem cpuPackagePowerMw_all_nonpositive_none (rows : List (String × Int))
    (hle : ∀ p ∈ rows, p.2 ≤ 0) :
    cpuPackagePowerMw rows = none := by
  have hnil : dedupPositiveRows rows = [] := by
    unfold dedupPositiveRows
    suffices ∀ seen rs, (∀ p ∈ rs, p.2 ≤ 0) → dedupPositiveRowsAux seen rs = [] by
      exact this [] rows hle
    intro seen rs hrs
    induction rs generalizing seen with
    | nil => rfl
    | cons r rest ih =>
      rcases r with ⟨rn, rv⟩
      have hrv : rv ≤ 0 := hrs (rn, rv) List.mem_cons_self
      simp only [dedupPositiveRowsAux, hrv, ↓reduceIte]
      exact ih seen (fun p hp => hrs p (List.mem_cons_of_mem _ hp))
  simp [cpuPackagePowerMw, hnil, selectFirstNonEmptyTier, tierValues, activeTiers]

-- ── Linux RAPL energy counter wrap-around ─────────────────────────────────────

/-- Matches `_linux_rapl_energy_delta(previous_uj, current_uj, max_range)`. -/
def linuxRaplEnergyDelta (prevUj currUj : Int) (maxRange : Option Int) : Option Nat :=
  if prevUj < 0 || currUj < 0 then
    none
  else
    let delta := currUj - prevUj
    if delta < 0 then
      match maxRange with
      | none => none
      | some mr =>
        if mr ≤ 0 then none
        else
          let wrapped := delta + mr
          if wrapped < 0 then none else some wrapped.toNat
    else
      some delta.toNat

/-- Theorem P6 (RAPL Wrap-Around Safety): For any valid readings `0 ≤ prev < maxRange` and
    `0 ≤ curr < maxRange`, `linuxRaplEnergyDelta` always succeeds with a bounded non-negative
    delta in `[0, maxRange)`. -/
theorem linuxRaplEnergyDelta_in_range
    (prev curr maxRange : Int)
    (hp0 : 0 ≤ prev) (hpM : prev < maxRange)
    (hc0 : 0 ≤ curr) (hcM : curr < maxRange) :
    ∃ d : Nat, linuxRaplEnergyDelta prev curr (some maxRange) = some d ∧
      d < maxRange.toNat := by
  unfold linuxRaplEnergyDelta
  have hnotneg : (prev < 0 || curr < 0) = false := by
    have h1 : decide (prev < 0) = false := by simp; omega
    have h2 : decide (curr < 0) = false := by simp; omega
    simp [h1, h2]
  simp only [hnotneg, Bool.false_eq_true, ↓reduceIte]
  by_cases hlt : curr - prev < 0
  · have hmr_pos : ¬(maxRange ≤ 0) := by omega
    have hwrap_nonneg : ¬(curr - prev + maxRange < 0) := by omega
    simp only [hlt, ↓reduceIte, hmr_pos, hwrap_nonneg]
    refine ⟨(curr - prev + maxRange).toNat, rfl, ?_⟩
    omega
  · simp only [hlt, ↓reduceIte]
    refine ⟨(curr - prev).toNat, rfl, ?_⟩
    omega

/-- Theorem P7 (Exact Wrap Formula): When the counter wraps (`0 ≤ curr < prev < maxRange`),
    the returned delta is exactly `(curr - prev + maxRange).toNat`. -/
theorem linuxRaplEnergyDelta_wrap_exact
    (prev curr maxRange : Int)
    (hc0 : 0 ≤ curr) (hwrap : curr < prev) (hpM : prev < maxRange) :
    linuxRaplEnergyDelta prev curr (some maxRange) = some (curr - prev + maxRange).toNat := by
  unfold linuxRaplEnergyDelta
  have h1 : decide (prev < 0) = false := by simp; omega
  have h2 : decide (curr < 0) = false := by simp; omega
  have hlt : curr - prev < 0 := by omega
  have hmr_pos : ¬(maxRange ≤ 0) := by omega
  have hwrap_nonneg : ¬(curr - prev + maxRange < 0) := by omega
  simp [h1, h2, hlt, hmr_pos, hwrap_nonneg]

end Tricorder.Power
