import Lean.Data.Json
import Tricorder

open Lean (Json)
open Tricorder.Layout
open Tricorder.Updater
open Tricorder.Power
open Tricorder.Config

private def getStrList (j : Json) (key : String) : Except String (List String) := do
  let v ← j.getObjVal? key
  let arr ← v.getArr?
  arr.toList.mapM Json.getStr?

private def getBoolList (j : Json) (key : String) : Except String (List Bool) := do
  let v ← j.getObjVal? key
  let arr ← v.getArr?
  arr.toList.mapM Json.getBool?

private def getNatList (j : Json) (key : String) : Except String (List Nat) := do
  let v ← j.getObjVal? key
  let arr ← v.getArr?
  arr.toList.mapM Json.getNat?

private def getOptStr (j : Json) (key : String) : Option String :=
  match j.getObjVal? key with
  | .ok (.str s) => some s
  | _ => none

private def getOptBool (j : Json) (key : String) : Option Bool :=
  match j.getObjVal? key with
  | .ok (.bool b) => some b
  | _ => none

private def getOptInt (j : Json) (key : String) : Option Int :=
  match j.getObjVal? key with
  | .ok v =>
    match v.getInt? with
    | .ok n => some n
    | .error _ => none
  | .error _ => none

private def getOptNat (j : Json) (key : String) : Option Nat :=
  match j.getObjVal? key with
  | .ok v =>
    match v.getNat? with
    | .ok n => some n
    | .error _ => none
  | .error _ => none

private def parseRawIntVal (j : Json) (key : String) : Option RawIntVal :=
  match j.getObjVal? key with
  | .error _ => none
  | .ok .null => none
  | .ok (.bool _) => some .invalid
  | .ok (.str s) => some (.strVal s)
  | .ok v =>
    match v.getInt? with
    | .ok n => some (.intVal n)
    | .error _ => some .invalid

private def parseTileColors (j : Json) (key : String) : List (String × Option String) :=
  match j.getObjVal? key with
  | .ok (.arr arr) =>
    arr.toList.filterMap fun item =>
      match item with
      | .arr #[.str k, .str v] => some (k, some v)
      | .arr #[.str k, _] => some (k, none)
      | _ => none
  | _ => []

private def parsePowerRows (j : Json) (key : String) : Except String (List (String × Int)) := do
  let v ← j.getObjVal? key
  let arr ← v.getArr?
  arr.toList.mapM fun item => do
    let pair ← item.getArr?
    match pair.toList with
    | [jn, jv] =>
      let name ← jn.getStr?
      let mw ← jv.getInt?
      return (name, mw)
    | _ => throw "expected [name, mw] pair"

private def parseExeEvent (j : Json) : Except String ExeEvent := do
  let kind ← (← j.getObjVal? "type").getStr?
  match kind with
  | "start_download" => return .startDownload
  | "finish_download" => return .finishDownload
  | "download_fail" => return .downloadFail
  | "validate" =>
    let sz ← (← j.getObjVal? "size_ok").getBool?
    let sh ← (← j.getObjVal? "sha256_ok").getBool?
    let pe ← (← j.getObjVal? "pe_version_ok").getBool?
    let st ← (← j.getObjVal? "self_test_ok").getBool?
    return .validate sz sh pe st
  | "stage_copy_ok" => return .stageCopyOk
  | "stage_copy_fail" => return .stageCopyFail
  | "guard_and_clear_old_backup" => return .guardAndClearOldBackup
  | "move_exe_to_backup_ok" => return .moveExeToBackupOk
  | "move_exe_to_backup_fail" => return .moveExeToBackupFail
  | "move_new_to_exe_ok" => return .moveNewToExeOk
  | "move_new_to_exe_fail" => return .moveNewToExeFail
  | "launch_new_ok" => return .launchNewOk
  | "launch_new_fail" => return .launchNewFail
  | "rollback_restore_ok" => return .rollbackRestoreOk
  | "rollback_restore_fail" => return .rollbackRestoreFail
  | "commit_cleanup_backup" => return .commitCleanupBackup
  | "crash" => return .crash
  | other => throw s!"unknown exe event: {other}"

private def artifactToJson : Option Artifact → Json
  | none => Json.null
  | some .old => Json.str "old"
  | some .unverified => Json.str "unverified"
  | some .verifiedNew => Json.str "verified_new"

private def phaseToStr : ExePhase → String
  | .idle => "idle"
  | .downloading => "downloading"
  | .validating => "validating"
  | .staged => "staged"
  | .backupCleared => "backup_cleared"
  | .backedUp => "backed_up"
  | .swapped => "swapped"
  | .waitingCommit => "waiting_commit"
  | .rollingBack => "rolling_back"
  | .committed => "committed"
  | .failed => "failed"
  | .crashed => "crashed"

private def fileVerToJson : Option FileVer → Json
  | none => Json.null
  | some .old => Json.str "old"
  | some .new => Json.str "new"

private def handleRequest (req : Json) : Except String Json := do
  let op ← (← req.getObjVal? "op").getStr?
  match op with
  | "layout_merge" =>
    let savedOrder ← getStrList req "saved_order"
    let savedHidden ← getStrList req "saved_hidden"
    let tileIds ← getStrList req "tile_ids"
    let defaultOrder ← getStrList req "default_order"
    let (order, hidden, changed) := mergeLayoutTiles savedOrder savedHidden tileIds defaultOrder
    return Json.mkObj [
      ("order", Json.arr (order.map Json.str).toArray),
      ("hidden", Json.arr (hidden.map Json.str).toArray),
      ("changed", Json.bool changed)
    ]
  | "layout_dormant" =>
    let activeOrder ← getStrList req "active_order"
    let activeHidden ← getStrList req "active_hidden"
    let storedOrder ← getStrList req "stored_order"
    let storedHidden ← getStrList req "stored_hidden"
    let activeIds ← getStrList req "active_ids"
    let (order, hidden) := preserveDormantLayout activeOrder activeHidden storedOrder storedHidden activeIds
    return Json.mkObj [
      ("order", Json.arr (order.map Json.str).toArray),
      ("hidden", Json.arr (hidden.map Json.str).toArray)
    ]
  | "layout_move" =>
    let tileOrder ← getStrList req "tile_order"
    let tileId ← (← req.getObjVal? "tile_id").getStr?
    let afterRowIdx ← (← req.getObjVal? "after_row_idx").getInt?
    let order := moveTileToNewRow tileOrder tileId afterRowIdx
    return Json.mkObj [
      ("order", Json.arr (order.map Json.str).toArray)
    ]
  | "power_pdh" =>
    let rows ← parsePowerRows req "rows"
    let res := cpuPackagePowerMw rows
    return Json.mkObj [
      ("power_mw", match res with | none => Json.null | some n => Json.num n)
    ]
  | "power_rapl" =>
    let prevUj ← (← req.getObjVal? "prev_uj").getInt?
    let currUj ← (← req.getObjVal? "curr_uj").getInt?
    let maxRange := getOptInt req "max_range"
    let res := linuxRaplEnergyDelta prevUj currUj maxRange
    return Json.mkObj [
      ("delta_uj", match res with | none => Json.null | some n => Json.num n)
    ]
  | "config_sanitize" =>
    let raw : RawConfig := {
      theme := getOptStr req "theme"
      minimizeToTray := getOptBool req "minimize_to_tray"
      closeToTray := getOptBool req "close_to_tray"
      autostart := getOptBool req "autostart"
      autostartMode := getOptStr req "autostart_mode"
      minRowH := parseRawIntVal req "min_row_h"
      tileColors := parseTileColors req "tile_colors"
    }
    let valid := sanitizeConfig raw
    let colorsObj := Json.mkObj (valid.tileColors.map fun (k, v) => (k, Json.str v))
    return Json.mkObj [
      ("theme", Json.str valid.theme.toString),
      ("minimize_to_tray", Json.bool valid.minimizeToTray),
      ("close_to_tray", Json.bool valid.closeToTray),
      ("autostart", Json.bool valid.autostart),
      ("autostart_mode", Json.str valid.autostartMode.toString),
      ("min_row_h", Json.num valid.minRowH),
      ("max_row_h", Json.num valid.maxRowH),
      ("tile_colors", colorsObj)
    ]
  | "updater_exe_trace" =>
    let evArr ← (← req.getObjVal? "events").getArr?
    let evs ← evArr.toList.mapM parseExeEvent
    let finalState := exeRunTrace ExeUpdateState.init evs
    let recoverable : Bool :=
      finalState.fs.exe == some .old ||
      finalState.fs.exe == some .verifiedNew ||
      finalState.fs.backup == some .old
    return Json.mkObj [
      ("phase", Json.str (phaseToStr finalState.phase)),
      ("exe", artifactToJson finalState.fs.exe),
      ("backup", artifactToJson finalState.fs.backup),
      ("recoverable", Json.bool recoverable),
      ("flags_all_passed", Json.bool finalState.flags.allPassed)
    ]
  | "updater_source_tx" =>
    let existedFlags ← getBoolList req "existed_flags"
    let failInstallAt := getOptNat req "fail_install_at"
    let failRollbackAt ← getNatList req "fail_rollback_at"
    let failCleanupAt ← getNatList req "fail_cleanup_at"
    let res := runSourceInstall existedFlags failInstallAt failRollbackAt failCleanupAt
    let outcomeStr := match res.outcome with | .committed => "committed" | .rolledBack => "rolled_back"
    let slotsJson := res.slots.map fun s =>
      Json.mkObj [
        ("existed", Json.bool s.existedOriginally),
        ("target", fileVerToJson s.target),
        ("backup", fileVerToJson s.backup)
      ]
    return Json.mkObj [
      ("outcome", Json.str outcomeStr),
      ("slots", Json.arr slotsJson.toArray)
    ]
  | other => throw s!"unknown op: {other}"

private partial def loop (stdin stdout : IO.FS.Stream) : IO Unit := do
  let line ← stdin.getLine
  if line.isEmpty then
    return ()
  let trimmed := line.trimAscii.toString
  if !trimmed.isEmpty then
    let response : Json :=
      match Json.parse trimmed with
      | .error err => Json.mkObj [("error", Json.str s!"json parse error: {err}")]
      | .ok req =>
        match handleRequest req with
        | .error err => Json.mkObj [("error", Json.str err)]
        | .ok res => res
    stdout.putStrLn response.compress
    stdout.flush
  loop stdin stdout

def main : IO Unit := do
  let stdin ← IO.getStdin
  let stdout ← IO.getStdout
  loop stdin stdout
