"""Windows-only controlled tests. Fixtures are retained; no installed runtime or registry is used."""
import collections
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("shuffles_under_test", ROOT / "shuffles.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)

class Crash(BaseException):
    pass

@unittest.skipUnless(os.name == "nt", "Windows rename/attribute behavior required")
class CoreTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "_test_artifacts"
        base.mkdir(exist_ok=True)
        self.case = Path(tempfile.mkdtemp(prefix="case-", dir=base))
        self.target = self.case / "Photos"
        self.target.mkdir()
        self.state = self.case / "state"
        self.state.mkdir()
        self.patch = mock.patch.object(s.tempfile, "gettempdir", return_value=str(self.state))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.dialog = mock.patch.object(s, "msgbox", side_effect=AssertionError("Unexpected GUI"))
        self.dialog.start()
        self.addCleanup(self.dialog.stop)

    def put(self, name, data=b"synthetic media"):
        path = self.target / name
        path.write_bytes(data)
        return path

    def inventory(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.target.iterdir()
                if p.is_file() and p.suffix.lower() in s.ELIGIBLE_EXTENSIONS}

    def plan(self, *names):
        entries=[]
        for i,name in enumerate(names,1):
            p=self.target/name
            entries.append({"original":name,"temp":f"{s.TEMP_PREFIX}{i:032x}{p.suffix.lower()}",
                            "final":f"photos-{i}{p.suffix.lower()}","identity":s._file_identity(p),
                            "was_hidden":s.is_hidden(p)})
        s.write_manifest(self.target,entries)
        return s.load_manifest(self.target)

    def test_shuffle_preserves_content_and_extension_sequences(self):
        for name,data in [("a.JPG",b"a"),("b.jpg",b"b"),("c.PNG",b"c"),("d.avif",b"d")]: self.put(name,data)
        before=collections.Counter(self.inventory().values())
        result=s.run_core(self.target)
        self.assertTrue(result["success"],result)
        self.assertEqual(set(self.inventory()),{"photos-1.jpg","photos-2.jpg","photos-1.png","photos-1.avif"})
        self.assertEqual(collections.Counter(self.inventory().values()),before)
        self.assertFalse(s.manifest_path(self.target).exists())
        self.assertTrue(list((self.state/".shuffles-archive").glob("*.json")))

    def test_repeat_runs_preserve_every_payload(self):
        for i in range(6):self.put(f"item-{i}.jpg",bytes([i]))
        before=collections.Counter(self.inventory().values())
        for _ in range(3):self.assertTrue(s.run_core(self.target)["success"])
        self.assertEqual(collections.Counter(self.inventory().values()),before)

    def test_hidden_media_is_included_and_unhidden(self):
        p=self.put("hidden.jpg");s.set_hidden(p);self.assertTrue(s.is_hidden(p))
        self.assertTrue(s.run_core(self.target)["success"])
        self.assertFalse(s.is_hidden(self.target/"photos-1.jpg"))

    def test_directories_reserve_final_names(self):
        (self.target/"photos-1.jpg").mkdir();self.put("input.jpg")
        self.assertTrue(s.run_core(self.target)["success"])
        self.assertTrue((self.target/"photos-1.jpg").is_dir())
        self.assertTrue((self.target/"photos-2.jpg").is_file())

    def test_windows_rename_never_replaces_existing_destination(self):
        a=self.put("a.jpg",b"source");z=self.put("z.jpg",b"foreign")
        with self.assertRaises(FileExistsError):a.rename(z)
        self.assertEqual(a.read_bytes(),b"source");self.assertEqual(z.read_bytes(),b"foreign")

    def test_checked_rename_refuses_late_collision(self):
        a=self.put("a.jpg",b"source");z=self.put("z.jpg",b"foreign")
        with self.assertRaises(FileExistsError):s._checked_rename(a,z,{"identity":s._file_identity(a)})
        self.assertEqual(a.read_bytes(),b"source");self.assertEqual(z.read_bytes(),b"foreign")

    def test_relative_root_rejected(self):self.assertFalse(s.run_core(Path("."))["success"])
    def test_drive_root_rejected(self):self.assertFalse(s.run_core(Path(self.target.anchor))["success"])
    def test_unc_root_rejected(self):self.assertFalse(s.run_core(Path(r"\\server\share"))["success"])
    def test_profile_root_rejected(self):self.assertFalse(s.run_core(Path.home())["success"])
    def test_system_root_rejected(self):self.assertFalse(s.run_core(Path(os.environ["SystemRoot"]))["success"])

    def test_non_media_and_subdirectories_unchanged(self):
        p=self.put("notes.txt",b"keep");sub=self.target/"child";sub.mkdir();q=sub/"x.jpg";q.write_bytes(b"nested")
        self.assertTrue(s.run_core(self.target)["success"])
        self.assertEqual(p.read_bytes(),b"keep");self.assertEqual(q.read_bytes(),b"nested")

    def test_casefolded_path_alias_shares_manifest(self):
        self.assertEqual(s.manifest_path(self.target),s.manifest_path(Path(str(self.target).swapcase())))

    def test_corrupt_manifest_preserved_and_aborts(self):
        self.put("a.jpg");mp=s.manifest_path(self.target);mp.write_text("{",encoding="utf-8")
        before=self.inventory();result=s.run_core(self.target)
        self.assertFalse(result["success"]);self.assertEqual(self.inventory(),before);self.assertEqual(mp.read_text(),"{")

    def test_legacy_manifest_is_not_silently_ignored(self):
        self.put("a.jpg");mp=s.manifest_path(self.target);mp.write_text(json.dumps({"entries":[]}),encoding="utf-8")
        self.assertFalse(s.run_core(self.target)["success"]);self.assertTrue(mp.exists())

    def test_manifest_wrong_root_rejected(self):
        self.put("a.jpg");m=self.plan("a.jpg");m["target_dir"]=str(self.case)
        with self.assertRaises(RuntimeError):s.validate_manifest(self.target,m)

    def test_manifest_traversal_absolute_and_ads_rejected(self):
        self.put("a.jpg");m=self.plan("a.jpg")
        for bad in ["../outside.jpg",r"..\outside.jpg",r"C:\outside.jpg","a.jpg:stream","nul.jpg","a.jpg."]:
            with self.subTest(bad=bad):
                changed=json.loads(json.dumps(m));changed["entries"][0]["original"]=bad
                with self.assertRaises(RuntimeError):s.validate_manifest(self.target,changed)

    def test_manifest_duplicate_names_rejected(self):
        self.put("a.jpg");self.put("b.jpg",b"b");m=self.plan("a.jpg","b.jpg")
        m["entries"][1]["original"]="A.JPG"
        with self.assertRaises(RuntimeError):s.validate_manifest(self.target,m)

    def test_manifest_duplicate_identity_rejected(self):
        self.put("a.jpg");self.put("b.jpg",b"b");m=self.plan("a.jpg","b.jpg")
        m["entries"][1]["identity"]=m["entries"][0]["identity"]
        with self.assertRaises(RuntimeError):s.validate_manifest(self.target,m)

    def test_missing_recovery_file_never_substitutes_same_name(self):
        p=self.put("a.jpg",b"original");m=self.plan("a.jpg")
        archive=self.case/"original-preserved.jpg";p.rename(archive);self.put("a.jpg",b"foreign")
        result=s.run_core(self.target,"rollback")
        self.assertFalse(result["success"]);self.assertEqual(p.read_bytes(),b"foreign");self.assertTrue(s.manifest_path(self.target).exists())

    def test_recovery_abort_changes_no_media(self):
        self.put("a.jpg");self.plan("a.jpg");before=self.inventory()
        result=s.run_core(self.target,"abort");self.assertFalse(result["success"]);self.assertEqual(self.inventory(),before)

    def test_lock_precedes_loading_or_discarding_recovery(self):
        self.put("a.jpg");self.plan("a.jpg");lock=s.acquire_lock(self.target)
        try:
            for action in ("abort","continue","rollback","discard"):
                with mock.patch.object(s,"load_manifest",side_effect=AssertionError("Loaded before lock")):
                    self.assertFalse(s.run_core(self.target,action)["success"])
            self.assertTrue(s.manifest_path(self.target).exists())
        finally:s.release_lock(lock)

    def test_atomic_manifest_failure_leaves_media_unchanged(self):
        self.put("a.jpg");before=self.inventory()
        with mock.patch.object(s.os,"replace",side_effect=OSError("injected flush/replace failure")):
            result=s.run_core(self.target)
        self.assertFalse(result["success"]);self.assertEqual(self.inventory(),before)

    def test_manifest_is_not_overwritten(self):
        self.put("a.jpg");m=self.plan("a.jpg");mp=s.manifest_path(self.target);before=mp.read_bytes()
        with self.assertRaises(RuntimeError):s.write_manifest(self.target,m["entries"])
        self.assertEqual(mp.read_bytes(),before)

    def test_pass_one_failure_rolls_back_without_content_loss(self):
        self.put("a.jpg",b"a");self.put("b.jpg",b"b");before=self.inventory();real=s._checked_rename;count=0
        def fail(source,destination,entry):
            nonlocal count
            if destination.name.startswith(s.TEMP_PREFIX):
                count+=1
                if count==2:raise OSError("injected pass1 failure")
            return real(source,destination,entry)
        with mock.patch.object(s,"_checked_rename",side_effect=fail):result=s.run_core(self.target)
        self.assertFalse(result["success"]);self.assertEqual(self.inventory(),before);self.assertFalse(s.manifest_path(self.target).exists())

    def interrupted(self,after_final=False,all_final=False):
        self.put("photos-1.jpg",b"first");self.put("photos-2.jpg",b"second");before=self.inventory();real=s._checked_rename
        def crash(source,destination,entry):
            if not destination.name.startswith(s.TEMP_PREFIX):
                if not after_final:raise Crash()
                result=real(source,destination,entry);raise Crash()
            return real(source,destination,entry)
        with mock.patch("random.shuffle",side_effect=lambda files:files.reverse()):
            if all_final:
                with mock.patch.object(s,"delete_manifest",side_effect=Crash()):
                    with self.assertRaises(Crash):s.run_core(self.target)
            else:
                with mock.patch.object(s,"_checked_rename",side_effect=crash):
                    with self.assertRaises(Crash):s.run_core(self.target)
        self.assertTrue(s.manifest_path(self.target).exists());self.assertFalse((self.target/s.LOCK_FILE).exists())
        return before

    def test_crash_after_pass_one_can_continue(self):
        before=self.interrupted();result=s.run_core(self.target,"continue")
        self.assertTrue(result["success"],result);self.assertEqual(collections.Counter(self.inventory().values()),collections.Counter(before.values()))

    def test_crash_mid_pass_two_rolls_back_cycles_by_identity(self):
        before=self.interrupted(after_final=True);result=s.run_core(self.target,"rollback")
        self.assertTrue(result["success"],result);self.assertEqual(self.inventory(),before)

    def test_crash_after_all_finals_rolls_back_original_names(self):
        before=self.interrupted(all_final=True);result=s.run_core(self.target,"rollback")
        self.assertTrue(result["success"],result);self.assertEqual(self.inventory(),before)

    def test_recovery_collision_keeps_foreign_entry_and_manifest(self):
        self.interrupted();foreign=self.put("photos-1.jpg",b"new arrival")
        result=s.run_core(self.target,"continue");self.assertFalse(result["success"])
        self.assertEqual(foreign.read_bytes(),b"new arrival");self.assertTrue(s.manifest_path(self.target).exists())
        foreign.rename(self.case/"arrival-preserved.jpg")
        self.assertTrue(s.run_core(self.target,"continue")["success"])

    def test_discard_archives_only_mapping_without_renaming_media(self):
        self.put("a.jpg");self.plan("a.jpg");before=self.inventory();result=s.run_core(self.target,"discard")
        self.assertTrue(result["success"]);self.assertEqual(self.inventory(),before);self.assertTrue(list((self.state/".shuffles-archive").glob("*.json")))

    def test_orphan_temporary_file_blocks_fresh_run(self):
        self.put(s.TEMP_PREFIX+"orphan.jpg");self.put("a.jpg");before=self.inventory()
        self.assertFalse(s.run_core(self.target)["success"]);self.assertEqual(self.inventory(),before)

    def test_log_can_be_updated_after_becoming_hidden(self):
        s.write_log(self.target,["first"]);self.assertTrue(s.is_hidden(self.target/s.LOG_FILE))
        s.write_log(self.target,["second"]);self.assertEqual((self.target/s.LOG_FILE).read_text(encoding="utf-8"),"second")

    def test_log_failure_before_any_media_rename(self):
        self.put("a.jpg");(self.target/s.LOG_FILE).mkdir();before=self.inventory()
        result=s.run_core(self.target);self.assertFalse(result["success"]);self.assertEqual(self.inventory(),before)

    def test_long_original_name_uses_short_temporary_name(self):
        self.put("a"*180+".jpg");result=s.run_core(self.target);self.assertTrue(result["success"],result)
        self.assertTrue((self.target/"photos-1.jpg").exists())

    def test_hardlink_aliases_are_skipped(self):
        p=self.put("a.jpg");os.link(p,self.target/"b.jpg");before=self.inventory()
        result=s.run_core(self.target);self.assertTrue(result["success"]);self.assertEqual(result["renamed"],0);self.assertEqual(self.inventory(),before)

    def test_rejected_target_is_not_enumerated(self):
        with mock.patch.object(s, "_validate_target", side_effect=RuntimeError("blocked")), mock.patch.object(s, "has_leftover_temps", side_effect=AssertionError("enumerated refused target")):
            self.assertFalse(s.run_core(self.target)["success"])

    def test_long_folder_base_stays_within_filename_limit(self):
        self.assertEqual(120,len(s.sanitize_name("X"*200)))

    def test_quiet_cli_outputs_json(self):
        self.put("a.jpg");output=io.StringIO()
        with mock.patch.object(sys,"argv",["shuffles.py","--quiet",str(self.target)]),contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exit:s.main()
        self.assertEqual(exit.exception.code,0);self.assertTrue(json.loads(output.getvalue())["success"])

    def test_gui_uses_core_recovery_under_lock(self):
        self.put("a.jpg");self.plan("a.jpg")
        def choose(target,manifest):
            self.assertTrue((target/s.LOCK_FILE).exists());return "discard"
        self.assertTrue(s.run_core(self.target,choose_recovery=choose)["success"])

if __name__=="__main__":unittest.main(verbosity=2)
