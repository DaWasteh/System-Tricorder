/-!
# Formal Specification & Proofs for System Tricorder Configuration Sanitizer

Models the pure configuration normalizers and total sanitizer in `system_tricorder.py`:
- `_normalise_theme`
- `_normalise_autostart_mode`
- `_normalise_color_hex`
- `_normalise_min_row_h`
- `_sanitise_config`

The dependent return type `ValidConfig` embeds all range and format proofs directly,
guaranteeing by construction that corrupt JSON inputs can never produce invalid internal state.
-/

namespace Tricorder.Config

def minRowHFloor : Nat := 50
def minRowHCeil : Nat := 400
def defaultMinRowH : Nat := 75
def defaultMaxRowH : Nat := 180

inductive Theme where
  | dark
  | light
  deriving DecidableEq, Repr

def Theme.toString : Theme → String
  | .dark => "dark"
  | .light => "light"

inductive AutostartMode where
  | normal
  | maximized
  | minimized
  deriving DecidableEq, Repr

def AutostartMode.toString : AutostartMode → String
  | .normal => "normal"
  | .maximized => "maximized"
  | .minimized => "minimized"

private def isWsChar (c : Char) : Bool :=
  c == ' ' || c == '\t' || c == '\n' || c == '\r'

private def lowerAsciiChar (c : Char) : Char :=
  if 65 ≤ c.toNat && c.toNat ≤ 90 then Char.ofNat (c.toNat + 32) else c

private def stripLower (s : String) : String :=
  let cs := ((s.toList.dropWhile isWsChar).reverse.dropWhile isWsChar).reverse
  String.ofList (cs.map lowerAsciiChar)

def normaliseTheme : Option String → Theme
  | some s => if stripLower s == "light" then .light else .dark
  | none => .dark

def normaliseAutostartMode : Option String → AutostartMode
  | some s =>
    let m := stripLower s
    if m == "maximized" then .maximized
    else if m == "minimized" then .minimized
    else .normal
  | none => .normal

private def isHexDigit (c : Char) : Bool :=
  (48 ≤ c.toNat && c.toNat ≤ 57) ||
  (97 ≤ c.toNat && c.toNat ≤ 102) ||
  (65 ≤ c.toNat && c.toNat ≤ 70)

def normaliseColorHex : Option String → Option String
  | none => none
  | some s =>
    let trimmed := ((s.toList.dropWhile isWsChar).reverse.dropWhile isWsChar).reverse
    match trimmed with
    | ['#' , c1, c2, c3, c4, c5, c6] =>
      if isHexDigit c1 && isHexDigit c2 && isHexDigit c3 &&
         isHexDigit c4 && isHexDigit c5 && isHexDigit c6 then
        some (String.ofList ('#' :: [c1, c2, c3, c4, c5, c6].map lowerAsciiChar))
      else none
    | ['#', r, g, b] =>
      if isHexDigit r && isHexDigit g && isHexDigit b then
        let r' := lowerAsciiChar r
        let g' := lowerAsciiChar g
        let b' := lowerAsciiChar b
        some (String.ofList ['#', r', r', g', g', b', b'])
      else none
    | _ => none

/-- Raw dynamic JSON value for `min_row_h`. -/
inductive RawIntVal where
  | intVal (n : Int)
  | strVal (s : String)
  | invalid
  deriving DecidableEq, Repr

private def isAsciiDigit (c : Char) : Bool := '0' ≤ c && c ≤ '9'

private def parseDigitsNat : List Char → Nat → Nat
  | [], acc => acc
  | c :: cs, acc => parseDigitsNat cs (acc * 10 + (c.toNat - '0'.toNat))

private def parseSignedIntString (s : String) : Option Int :=
  let trimmed := ((s.toList.dropWhile isWsChar).reverse.dropWhile isWsChar).reverse
  match trimmed with
  | '+' :: ds =>
    if !ds.isEmpty && ds.all isAsciiDigit then some (Int.ofNat (parseDigitsNat ds 0))
    else none
  | '-' :: ds =>
    if !ds.isEmpty && ds.all isAsciiDigit then some (-(Int.ofNat (parseDigitsNat ds 0)))
    else none
  | ds =>
    if !ds.isEmpty && ds.all isAsciiDigit then some (Int.ofNat (parseDigitsNat ds 0))
    else none

def clampIntToNat (v : Int) (low high : Nat) : Nat :=
  if v ≤ Int.ofNat low then low
  else if Int.ofNat high ≤ v then high
  else v.toNat

def normaliseMinRowH
    (value : Option RawIntVal)
    (defaultVal : Nat := defaultMinRowH)
    (floor : Nat := minRowHFloor)
    (ceil : Nat := minRowHCeil) : Nat :=
  let low := max 1 floor
  let high := max low ceil
  let fallback := max low (min defaultVal high)
  let parsed : Option Int :=
    match value with
    | none => none
    | some .invalid => none
    | some (.intVal n) => some n
    | some (.strVal s) => parseSignedIntString s
  match parsed with
  | none => fallback
  | some n => clampIntToNat n low high

/-- Arbitrary (possibly corrupted) configuration input. -/
structure RawConfig where
  theme : Option String := none
  minimizeToTray : Option Bool := none
  closeToTray : Option Bool := none
  autostart : Option Bool := none
  autostartMode : Option String := none
  minRowH : Option RawIntVal := none
  tileColors : List (String × Option String) := []
  deriving DecidableEq, Repr

/-- Sanitize tile-color entries (last valid entry per key wins, matching Python `dict` assignment). -/
def upsertColor (k v : String) : List (String × String) → List (String × String)
  | [] => [(k, v)]
  | (k', v') :: rest =>
    if k == k' then (k', v) :: rest
    else (k', v') :: upsertColor k v rest

def sanitiseTileColorsAux (acc : List (String × String)) :
    List (String × Option String) → List (String × String)
  | [] => acc
  | (rawId, rawColor) :: rest =>
    match normaliseColorHex rawColor with
    | none => sanitiseTileColorsAux acc rest
    | some hex =>
      if !rawId.isEmpty && rawId.length ≤ 256 then
        sanitiseTileColorsAux (upsertColor rawId hex acc) rest
      else
        sanitiseTileColorsAux acc rest

def sanitiseTileColors (entries : List (String × Option String)) : List (String × String) :=
  sanitiseTileColorsAux [] entries

-- ── Helper lemmas ─────────────────────────────────────────────────────────────

private theorem isWsChar_false_of_toNat_ge (c : Char) (h : 48 ≤ c.toNat) :
    isWsChar c = false := by
  unfold isWsChar
  simp only [Bool.or_eq_false_iff, beq_eq_false_iff_ne]
  refine ⟨⟨⟨?_, ?_⟩, ?_⟩, ?_⟩ <;> (intro heq; subst heq; simp at h)

private theorem lowerAsciiChar_hex (c : Char) (h : isHexDigit c = true) :
    isHexDigit (lowerAsciiChar c) = true ∧
    lowerAsciiChar (lowerAsciiChar c) = lowerAsciiChar c ∧
    isWsChar (lowerAsciiChar c) = false := by
  have hc : (48 ≤ c.toNat ∧ c.toNat ≤ 57) ∨ (97 ≤ c.toNat ∧ c.toNat ≤ 102) ∨ (65 ≤ c.toNat ∧ c.toNat ≤ 70) := by
    unfold isHexDigit at h
    simp only [Bool.or_eq_true, Bool.and_eq_true, decide_eq_true_eq] at h
    rcases h with (h1 | h2) | h3
    · exact Or.inl h1
    · exact Or.inr (Or.inl h2)
    · exact Or.inr (Or.inr h3)
  rcases hc with ⟨h1, h2⟩ | ⟨h1, h2⟩ | ⟨h1, h2⟩
  · have hb : (65 ≤ c.toNat && c.toNat ≤ 90) = false := by
      simp only [Bool.and_eq_false_iff, decide_eq_false_iff_not]; omega
    have hlow : lowerAsciiChar c = c := by simp [lowerAsciiChar, hb]
    rw [hlow, hlow]
    exact ⟨h, rfl, isWsChar_false_of_toNat_ge c (by omega)⟩
  · have hb : (65 ≤ c.toNat && c.toNat ≤ 90) = false := by
      simp only [Bool.and_eq_false_iff, decide_eq_false_iff_not]; omega
    have hlow : lowerAsciiChar c = c := by simp [lowerAsciiChar, hb]
    rw [hlow, hlow]
    exact ⟨h, rfl, isWsChar_false_of_toNat_ge c (by omega)⟩
  · have hb : (65 ≤ c.toNat && c.toNat ≤ 90) = true := by
      simp only [Bool.and_eq_true, decide_eq_true_eq]; omega
    have hlow : lowerAsciiChar c = Char.ofNat (c.toNat + 32) := by simp [lowerAsciiChar, hb]
    have hnat : (Char.ofNat (c.toNat + 32)).toNat = c.toNat + 32 := by
      simp only [Char.toNat] at h1 h2 ⊢
      have hval : (c.val.toNat + 32).isValidChar := Or.inl (by omega)
      unfold Char.ofNat
      simp only [hval, ↓reduceDIte, Char.ofNatAux]
      rfl
    rw [hlow]
    have hishex : isHexDigit (Char.ofNat (c.toNat + 32)) = true := by
      unfold isHexDigit
      simp only [Bool.or_eq_true, Bool.and_eq_true, decide_eq_true_eq, hnat]
      exact Or.inl (Or.inr (by omega))
    have hb2 : (65 ≤ (Char.ofNat (c.toNat + 32)).toNat && (Char.ofNat (c.toNat + 32)).toNat ≤ 90) = false := by
      simp only [Bool.and_eq_false_iff, decide_eq_false_iff_not, hnat]; omega
    have hlow2 : lowerAsciiChar (Char.ofNat (c.toNat + 32)) = Char.ofNat (c.toNat + 32) := by
      simp [lowerAsciiChar, hb2]
    exact ⟨hishex, hlow2, isWsChar_false_of_toNat_ge _ (by omega)⟩

/-- Theorem C1: `normaliseColorHex` is idempotent on every valid color output. -/
theorem normaliseColorHex_idempotent (raw : Option String) (hex : String)
    (h : normaliseColorHex raw = some hex) :
    normaliseColorHex (some hex) = some hex := by
  cases raw with
  | none => simp [normaliseColorHex] at h
  | some s =>
    unfold normaliseColorHex at h
    dsimp only at h
    split at h
    · rename_i c1 c2 c3 c4 c5 c6 _
      split at h
      · rename_i hhex
        simp only [Option.some.injEq] at h; subst h
        simp only [Bool.and_eq_true] at hhex
        have ⟨h1a, h1b, _⟩ := lowerAsciiChar_hex c1 hhex.1.1.1.1.1
        have ⟨h2a, h2b, _⟩ := lowerAsciiChar_hex c2 hhex.1.1.1.1.2
        have ⟨h3a, h3b, _⟩ := lowerAsciiChar_hex c3 hhex.1.1.1.2
        have ⟨h4a, h4b, _⟩ := lowerAsciiChar_hex c4 hhex.1.1.2
        have ⟨h5a, h5b, _⟩ := lowerAsciiChar_hex c5 hhex.1.2
        have ⟨h6a, h6b, h6w⟩ := lowerAsciiChar_hex c6 hhex.2
        have hhash : isWsChar '#' = false := by decide
        dsimp only [normaliseColorHex]
        rw [String.toList_ofList]
        simp [hhash, h6w, h1a, h2a, h3a, h4a, h5a, h6a, h1b, h2b, h3b, h4b, h5b, h6b]
      · simp at h
    · rename_i r g b _
      split at h
      · rename_i hhex
        simp only [Option.some.injEq] at h; subst h
        simp only [Bool.and_eq_true] at hhex
        have ⟨hra, hrb, _⟩ := lowerAsciiChar_hex r hhex.1.1
        have ⟨hga, hgb, _⟩ := lowerAsciiChar_hex g hhex.1.2
        have ⟨hba, hbb, hbw⟩ := lowerAsciiChar_hex b hhex.2
        have hhash : isWsChar '#' = false := by decide
        dsimp only [normaliseColorHex]
        rw [String.toList_ofList]
        simp [hhash, hbw, hra, hga, hba, hrb, hgb, hbb]
      · simp at h
    · simp at h

/-- Theorem C2: `normaliseMinRowH` always returns a value inside `[max 1 floor, max (max 1 floor) ceil]`. -/
theorem normaliseMinRowH_bounds (v : Option RawIntVal) (defaultVal floor ceil : Nat) :
    max 1 floor ≤ normaliseMinRowH v defaultVal floor ceil ∧
    normaliseMinRowH v defaultVal floor ceil ≤ max (max 1 floor) ceil := by
  unfold normaliseMinRowH
  dsimp only
  split
  · omega
  · rename_i n _
    unfold clampIntToNat
    by_cases h1 : n ≤ Int.ofNat (max 1 floor)
    · simp only [h1, ↓reduceIte]; omega
    · simp only [h1, ↓reduceIte]
      by_cases h2 : Int.ofNat (max (max 1 floor) ceil) ≤ n
      · simp only [h2, ↓reduceIte]; omega
      · simp only [h2, ↓reduceIte]
        simp only [Int.ofNat_eq_natCast] at h1 h2
        omega

private theorem upsertColor_spec (k v : String) (acc : List (String × String))
    (hvalid : ∀ p ∈ acc, p.1 ≠ "" ∧ p.1.length ≤ 256 ∧ normaliseColorHex (some p.2) = some p.2)
    (hnd : (acc.map Prod.fst).Nodup)
    (hk_ne : k ≠ "") (hk_len : k.length ≤ 256)
    (hv : normaliseColorHex (some v) = some v) :
    (∀ p ∈ upsertColor k v acc, p.1 ≠ "" ∧ p.1.length ≤ 256 ∧ normaliseColorHex (some p.2) = some p.2) ∧
    ((upsertColor k v acc).map Prod.fst).Nodup ∧
    (∀ x, x ∈ (upsertColor k v acc).map Prod.fst ↔ x = k ∨ x ∈ acc.map Prod.fst) := by
  induction acc with
  | nil =>
    simp [upsertColor, hk_ne, hk_len, hv]
  | cons p rest ih =>
    rcases p with ⟨k', v'⟩
    rw [List.map_cons, List.nodup_cons] at hnd
    have hp_val := hvalid (k', v') List.mem_cons_self
    have hrest_val : ∀ q ∈ rest, q.1 ≠ "" ∧ q.1.length ≤ 256 ∧ normaliseColorHex (some q.2) = some q.2 :=
      fun q hq => hvalid q (List.mem_cons_of_mem _ hq)
    simp only [upsertColor]
    by_cases hkk : k = k'
    · subst hkk
      simp only [beq_self_eq_true, ↓reduceIte]
      refine ⟨?_, ?_, ?_⟩
      · intro q hq
        rw [List.mem_cons] at hq
        rcases hq with rfl | hr
        · exact ⟨hk_ne, hk_len, hv⟩
        · exact hrest_val q hr
      · rw [List.map_cons, List.nodup_cons]
        exact hnd
      · intro x; simp [List.mem_cons]
    · have hbeq : (k == k') = false := beq_false_of_ne hkk
      simp only [hbeq, Bool.false_eq_true, ↓reduceIte]
      have ⟨h1, h2, h3⟩ := ih hrest_val hnd.2
      refine ⟨?_, ?_, ?_⟩
      · intro q hq
        rw [List.mem_cons] at hq
        rcases hq with rfl | hr
        · exact hp_val
        · exact h1 q hr
      · rw [List.map_cons, List.nodup_cons]
        refine ⟨?_, h2⟩
        intro hmem
        rw [h3] at hmem
        rcases hmem with heq | hrest
        · exact hkk heq.symm
        · exact hnd.1 hrest
      · intro x
        rw [List.map_cons, List.mem_cons, h3, List.map_cons, List.mem_cons]
        grind

private theorem sanitiseTileColorsAux_spec (acc : List (String × String))
    (entries : List (String × Option String))
    (hvalid : ∀ p ∈ acc, p.1 ≠ "" ∧ p.1.length ≤ 256 ∧ normaliseColorHex (some p.2) = some p.2)
    (hnd : (acc.map Prod.fst).Nodup) :
    (∀ p ∈ sanitiseTileColorsAux acc entries,
      p.1 ≠ "" ∧ p.1.length ≤ 256 ∧ normaliseColorHex (some p.2) = some p.2) ∧
    ((sanitiseTileColorsAux acc entries).map Prod.fst).Nodup := by
  induction entries generalizing acc with
  | nil => exact ⟨hvalid, hnd⟩
  | cons e rest ih =>
    rcases e with ⟨rawId, rawColor⟩
    simp only [sanitiseTileColorsAux]
    cases hhex : normaliseColorHex rawColor with
    | none => exact ih acc hvalid hnd
    | some hex =>
      dsimp only
      by_cases hkey : !rawId.isEmpty && rawId.length ≤ 256
      · simp only [hkey, ↓reduceIte]
        simp only [Bool.and_eq_true, Bool.not_eq_true', decide_eq_true_eq] at hkey
        have hne : rawId ≠ "" := by
          intro h; subst h; simp at hkey
        have hhex_idem := normaliseColorHex_idempotent rawColor hex hhex
        have ⟨hv', hnd', _⟩ := upsertColor_spec rawId hex acc hvalid hnd hne hkey.2 hhex_idem
        exact ih (upsertColor rawId hex acc) hv' hnd'
      · simp only [hkey, Bool.false_eq_true, ↓reduceIte]
        exact ih acc hvalid hnd

-- ── Validated Configuration (Dependent Type with Embedded Invariants) ─────────

structure ValidConfig where
  theme : Theme
  minimizeToTray : Bool
  closeToTray : Bool
  autostart : Bool
  autostartMode : AutostartMode
  minRowH : Nat
  maxRowH : Nat
  tileColors : List (String × String)
  hMinLower : minRowHFloor ≤ minRowH
  hMinUpper : minRowH ≤ minRowHCeil
  hMaxGeMin : minRowH ≤ maxRowH
  hColorsValid : ∀ p ∈ tileColors, p.1 ≠ "" ∧ p.1.length ≤ 256 ∧ normaliseColorHex (some p.2) = some p.2
  hColorKeysNodup : (tileColors.map Prod.fst).Nodup

/-- Theorem C3 (Total Config Sanitizer): Maps ANY arbitrary/corrupt `RawConfig` into a
    `ValidConfig` carrying proofs of all range, format, and uniqueness invariants. -/
def sanitizeConfig (raw : RawConfig) : ValidConfig :=
  let minH := normaliseMinRowH raw.minRowH defaultMinRowH minRowHFloor minRowHCeil
  have hBounds := normaliseMinRowH_bounds raw.minRowH defaultMinRowH minRowHFloor minRowHCeil
  let maxH := max defaultMaxRowH minH
  let colors := sanitiseTileColors raw.tileColors
  have hCol := sanitiseTileColorsAux_spec [] raw.tileColors (by simp) (by simp)
  { theme := normaliseTheme raw.theme
  , minimizeToTray := raw.minimizeToTray == some true
  , closeToTray := raw.closeToTray == some true
  , autostart := raw.autostart == some true
  , autostartMode := normaliseAutostartMode raw.autostartMode
  , minRowH := minH
  , maxRowH := maxH
  , tileColors := colors
  , hMinLower := hBounds.1
  , hMinUpper := hBounds.2
  , hMaxGeMin := Nat.le_max_right defaultMaxRowH minH
  , hColorsValid := hCol.1
  , hColorKeysNodup := hCol.2 }

def ValidConfig.toRaw (c : ValidConfig) : RawConfig :=
  { theme := some c.theme.toString
  , minimizeToTray := some c.minimizeToTray
  , closeToTray := some c.closeToTray
  , autostart := some c.autostart
  , autostartMode := some c.autostartMode.toString
  , minRowH := some (.intVal (Int.ofNat c.minRowH))
  , tileColors := c.tileColors.map (fun (k, v) => (k, some v)) }

private theorem upsertColor_of_not_mem (k v : String) (acc : List (String × String))
    (hnot : k ∉ acc.map Prod.fst) :
    upsertColor k v acc = acc ++ [(k, v)] := by
  induction acc with
  | nil => rfl
  | cons p rest ih =>
    rcases p with ⟨k', v'⟩
    rw [List.map_cons, List.mem_cons] at hnot
    have hne : k ≠ k' := fun h => hnot (Or.inl h)
    have hbeq : (k == k') = false := beq_false_of_ne hne
    simp only [upsertColor, hbeq, Bool.false_eq_true, ↓reduceIte, List.cons_append]
    rw [ih (fun h => hnot (Or.inr h))]

private theorem sanitiseTileColorsAux_valid_idempotent
    (acc valid : List (String × String))
    (hvalid : ∀ p ∈ valid, p.1 ≠ "" ∧ p.1.length ≤ 256 ∧ normaliseColorHex (some p.2) = some p.2)
    (hnd : (valid.map Prod.fst).Nodup)
    (hdisj : ∀ k ∈ valid.map Prod.fst, k ∉ acc.map Prod.fst) :
    sanitiseTileColorsAux acc (valid.map (fun (k, v) => (k, some v))) = acc ++ valid := by
  induction valid generalizing acc with
  | nil => simp [sanitiseTileColorsAux]
  | cons p rest ih =>
    rcases p with ⟨k, v⟩
    rw [List.map_cons, List.nodup_cons] at hnd
    have ⟨hk_ne, hk_len, hv⟩ := hvalid (k, v) List.mem_cons_self
    have hkey : (!k.isEmpty && k.length ≤ 256) = true := by
      have hne_b : k.isEmpty = false := by
        simp [String.isEmpty, hk_ne]
      simp [hne_b, hk_len]
    simp only [List.map_cons, sanitiseTileColorsAux, hv, hkey, ↓reduceIte]
    have hkn : k ∉ acc.map Prod.fst := hdisj k List.mem_cons_self
    rw [upsertColor_of_not_mem k v acc hkn]
    rw [ih (acc ++ [(k, v)])
      (fun q hq => hvalid q (List.mem_cons_of_mem _ hq))
      hnd.2
      (fun x hx hmem => by
        rw [List.map_append, List.mem_append, List.map_cons, List.map_nil, List.mem_singleton] at hmem
        rcases hmem with ha | rfl
        · exact hdisj x (List.mem_cons_of_mem k hx) ha
        · exact hnd.1 hx)]
    simp [List.append_assoc]

private theorem parseInt_ofNat_clamped (n : Nat)
    (hlo : minRowHFloor ≤ n) (hhi : n ≤ minRowHCeil) :
    normaliseMinRowH (some (.intVal (Int.ofNat n))) defaultMinRowH minRowHFloor minRowHCeil = n := by
  unfold minRowHFloor at hlo
  unfold minRowHCeil at hhi
  unfold normaliseMinRowH clampIntToNat minRowHFloor minRowHCeil
  dsimp only
  change (if Int.ofNat n ≤ Int.ofNat 50 then 50 else if Int.ofNat 400 ≤ Int.ofNat n then 400 else (Int.ofNat n).toNat) = n
  by_cases h1 : Int.ofNat n ≤ Int.ofNat 50
  · simp only [h1, ↓reduceIte]
    have h1' : n ≤ 50 := Int.ofNat_le.mp h1
    omega
  · simp only [h1, ↓reduceIte]
    by_cases h2 : Int.ofNat 400 ≤ Int.ofNat n
    · simp only [h2, ↓reduceIte]
      have h2' : 400 ≤ n := Int.ofNat_le.mp h2
      omega
    · simp only [h2, ↓reduceIte]
      rfl

/-- Theorem C4 (Valid Config Preservation / Idempotency): Sanitizing an already-valid
    configuration preserves every single user setting unchanged. -/
theorem sanitizeConfig_preserves_valid (c : ValidConfig) :
    let c' := sanitizeConfig c.toRaw
    c'.theme = c.theme ∧
    c'.minimizeToTray = c.minimizeToTray ∧
    c'.closeToTray = c.closeToTray ∧
    c'.autostart = c.autostart ∧
    c'.autostartMode = c.autostartMode ∧
    c'.minRowH = c.minRowH ∧
    c'.tileColors = c.tileColors := by
  unfold sanitizeConfig ValidConfig.toRaw
  dsimp only
  refine ⟨?_, ?_, ?_, ?_, ?_, ?_, ?_⟩
  · cases c.theme <;> decide
  · cases c.minimizeToTray <;> rfl
  · cases c.closeToTray <;> rfl
  · cases c.autostart <;> rfl
  · cases c.autostartMode <;> decide
  · exact parseInt_ofNat_clamped c.minRowH c.hMinLower c.hMinUpper
  · unfold sanitiseTileColors
    simpa using sanitiseTileColorsAux_valid_idempotent [] c.tileColors
      c.hColorsValid c.hColorKeysNodup (by simp)

end Tricorder.Config
