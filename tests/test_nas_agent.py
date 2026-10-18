from tests.testing_support import cleanup_after_idle
import json,os,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import patch
from nas_agent import Executor,make_server
from docker_client import AgentClient
class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.source=self.root/"source";self.source.mkdir()
        self.dest=self.root/"links";self.state=self.root/"state"
        self.agent=Executor(self.source,self.dest,self.state)
    def tearDown(self):cleanup_after_idle(self,self.temp,self.agent.lock)
    def file(self,name="movie.mp4",data=b"video"):
        p=self.source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data);return p
    def run_plan(self,plan):
        job=self.agent.start(plan["plan_id"],"CREATE_HARDLINKS")
        for _ in range(300):
            state=self.agent.job(job["job_id"])
            if state["done"] and not self.agent.lock.locked():return self.agent.job(job["job_id"])
            time.sleep(.01)
        self.fail("Job timeout")
    def test_preview_readonly_and_excludes_trash(self):
        self.file();self.file(".Trash-0/files/deleted.mp4")
        plan=self.agent.preview()
        self.assertEqual([r["rel"] for r in plan["rows"]],["movie.mp4"])
        self.assertEqual(plan["skipped"],1);self.assertFalse(self.dest.exists());self.assertFalse(self.state.exists())
    def test_creation_and_rerun_verify_real_inode(self):
        src=self.file();self.file("movie.jpg",b"image")
        plan=self.agent.preview();job=self.run_plan(plan)
        self.assertEqual(job["error"],"");self.assertEqual(job["created"],2)
        self.assertTrue(os.path.samefile(src,self.dest/"movie.mp4"))
        self.assertTrue(all(r["status"]=="已有硬链接" for r in self.agent.preview()["rows"]))
    def test_changed_source_stops_without_copy(self):
        src=self.file();plan=self.agent.preview();src.write_bytes(b"changed-content")
        job=self.run_plan(plan)
        self.assertTrue(job["error"]);self.assertEqual(job["created"],0);self.assertFalse(self.dest.exists())
    def test_conflict_not_overwritten(self):
        self.file();self.dest.mkdir();target=self.dest/"movie.mp4";target.write_bytes(b"keep")
        plan=self.agent.preview();self.assertEqual(plan["rows"][0]["status"],"目标冲突")
        with self.assertRaises(ValueError):self.agent.start(plan["plan_id"],"CREATE_HARDLINKS")
        self.assertEqual(target.read_bytes(),b"keep")
    def test_late_conflict_not_overwritten(self):
        self.file();plan=self.agent.preview();self.dest.mkdir();target=self.dest/"movie.mp4";target.write_bytes(b"keep")
        job=self.run_plan(plan);self.assertTrue(job["error"]);self.assertEqual(target.read_bytes(),b"keep")
    def test_confirmation_and_single_use(self):
        self.file();plan=self.agent.preview()
        with self.assertRaises(ValueError):self.agent.start(plan["plan_id"],"yes")
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.run_plan(plan)["error"],"")
        with self.assertRaises(ValueError):self.agent.start(plan["plan_id"],"CREATE_HARDLINKS")
    def test_link_failure_never_copies(self):
        self.file();plan=self.agent.preview()
        with patch("nas_agent.os.link",side_effect=OSError("hardlinks unsupported")):
            job=self.run_plan(plan)
        self.assertTrue(job["error"]);self.assertEqual(job["created"],0);self.assertFalse((self.dest/"movie.mp4").exists())
    def test_parent_device_mismatch_does_not_preempt_syscall(self):
        src=self.file();plan=self.agent.preview();real_stat=Path.stat;real_fstat=os.fstat
        def virtual_parent(path,*args,**kwargs):
            result=real_stat(path,*args,**kwargs)
            if path==self.dest and kwargs.get('follow_symlinks',True):
                values=list(result);values[2]=result.st_dev+1
                return os.stat_result(values)
            return result
        def virtual_fd(fd):
            result=real_fstat(fd)
            if self.dest.exists():
                parent=real_stat(self.dest)
                if (result.st_dev,result.st_ino)==(parent.st_dev,parent.st_ino):
                    values=list(result);values[2]=result.st_dev+1
                    return os.stat_result(values)
            return result
        with patch('nas_agent.Path.stat',virtual_parent),patch('safe_fs.os.fstat',virtual_fd):
            job=self.run_plan(plan)
        self.assertEqual(job['error'],'');self.assertEqual(job['created'],1)
        self.assertTrue(os.path.samefile(src,self.dest/'movie.mp4'))
    def test_actual_exdev_is_clear_and_never_copies(self):
        import errno
        self.file();plan=self.agent.preview()
        with patch('nas_agent.os.link',side_effect=OSError(errno.EXDEV,'Invalid cross-device link')) as link:
            job=self.run_plan(plan)
        link.assert_called_once();self.assertIn('EXDEV',job['error'])
        self.assertEqual(job['created'],0);self.assertFalse((self.dest/'movie.mp4').exists())
    def test_overlapping_roots_rejected(self):
        with self.assertRaises(ValueError):Executor(self.source,self.source/"nested",self.state)
    def test_long_name_and_paired_image(self):
        stem="作品标题"*20
        self.file(stem+".mp4");self.file(stem+".jpg")
        rows=self.agent.preview()["rows"]
        self.assertEqual(Path(rows[0]["newrel"]).stem,Path(rows[1]["newrel"]).stem)
        self.assertLessEqual(len(Path(rows[0]["newrel"]).stem.encode()),232)
    def test_names_below_budget_preserved(self):
        name='作品'*35+'.mp4'
        self.file(name);plan=self.agent.preview()
        self.assertEqual(plan['rows'][0]['newrel'],name)
    def test_exact_budget_filename_preserved(self):
        from link_rules import target_relative
        name='x'*236+'.mp4'
        self.assertEqual(target_relative(name),name)
    def test_long_names_keep_date(self):
        from link_rules import target_relative
        rel='作品'*70+' 2025-10-08.mp4'
        result=target_relative(rel)
        self.assertLessEqual(len(result.encode()),240)
        self.assertIn('2025-10-08',result)
        self.assertGreater(len(result.encode()),200)
    def test_legacy_existing_link_reused_without_duplicates(self):
        from link_rules import legacy_target_relative
        name='作品'*40+'.mp4';src=self.file(name)
        old=self.dest/legacy_target_relative(name);old.parent.mkdir(parents=True);os.link(src,old)
        row=self.agent.preview()['rows'][0]
        self.assertEqual(row['status'],'已有硬链接');self.assertEqual(row['dest'],str(old))
        self.assertEqual(len(list(self.dest.iterdir())),1)
    def test_change_budget_keeps_existing_recorded_link(self):
        self.file('标题'*40+'.mp4');first=self.agent.preview(240)
        self.assertEqual(self.run_plan(first)['error'],'')
        next_plan=self.agent.preview(180)
        self.assertEqual(next_plan['rows'][0]['status'],'已有硬链接')
        self.assertEqual(next_plan['rows'][0]['dest'],first['rows'][0]['dest'])
    def test_invalid_budget_rejected(self):
        for budget in (79,241,True,'240'):
            with self.subTest(budget=budget):
                with self.assertRaises(ValueError):self.agent.preview(budget)
    def test_state_cannot_overlap_source_or_destination(self):
        for state in (self.source,self.source/'logs',self.dest,self.dest/'logs',self.root):
            with self.subTest(state=state):
                with self.assertRaises(ValueError):Executor(self.source,self.dest,state)
    def test_parent_traversal_cannot_write_into_source(self):
        self.file();plan=self.agent.preview()
        self.agent.plans[plan['plan_id']]['rows'][0]['dest']=str(self.dest/'..'/'source'/'injected.mp4')
        job=self.run_plan(plan)
        self.assertTrue(job['error']);self.assertFalse((self.source/'injected.mp4').exists())
    def test_source_files_remain_named_and_unchanged(self):
        import hashlib
        original=self.file('原始标题.mp4',b'preserve-original')
        before=hashlib.sha256(original.read_bytes()).digest();names=set(self.source.iterdir())
        with patch('nas_agent.os.unlink',side_effect=AssertionError('Must not delete')), patch('nas_agent.os.rename',side_effect=AssertionError('Must not rename')), patch('nas_agent.os.replace',side_effect=AssertionError('Must not replace')):
            job=self.run_plan(self.agent.preview())
        self.assertEqual(job['error'],'');self.assertEqual(set(self.source.iterdir()),names)
        self.assertEqual(hashlib.sha256(original.read_bytes()).digest(),before)
    def test_shared_journal_cannot_modify_original(self):
        src=self.file();self.state.mkdir();os.link(src,self.state/'created.jsonl')
        with self.assertRaises(ValueError):self.agent.preview()
        self.assertEqual(src.read_bytes(),b'video')
    def test_destination_creation_stays_within_root(self):
        from nas_agent import ensure_destination
        with self.assertRaises(ValueError):ensure_destination(self.dest,self.source/'newdir')
        self.assertFalse((self.source/'newdir').exists())
    def test_expired_plan_cannot_create(self):
        self.file();plan=self.agent.preview();self.agent.plans[plan["plan_id"]]["created_at"]-=1801
        with self.assertRaisesRegex(ValueError,"expired"):self.agent.start(plan["plan_id"],"CREATE_HARDLINKS")
        self.assertFalse(self.dest.exists())
    def test_unapproved_plan_cannot_create(self):
        with self.assertRaises(ValueError):self.agent.start("invented-plan","CREATE_HARDLINKS")
        self.assertFalse(self.dest.exists())
    def test_source_symlink_skipped(self):
        target=self.file();link=self.source/"alias.mp4"
        try:link.symlink_to(target)
        except OSError:self.skipTest("OS denied local symlink creation")
        plan=self.agent.preview();self.assertEqual([r["rel"] for r in plan["rows"]],["movie.mp4"])
    def test_destination_symlink_rejected(self):
        outside=self.root/"outside";outside.mkdir()
        try:self.dest.symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest("OS denied local symlink creation")
        with self.assertRaises(ValueError):self.agent.preview()
    def test_http_auth_and_fixed_directories(self):
        self.file();server=make_server(self.agent,"a"*32,("127.0.0.1",0))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            url="http://127.0.0.1:"+str(server.server_port)
            bad=AgentClient(url,"wrong")
            with self.assertRaisesRegex(RuntimeError,"密钥"):bad.call("/preview",{})
            client=AgentClient(url,"a"*32)
            self.assertEqual(client.call("/health")["backend"],"NAS-local os.link")
            with self.assertRaisesRegex(RuntimeError,"directories"):client.call("/preview",{"source":"/other"})
            plan=client.call("/preview",{})
            self.assertEqual(len(plan["rows"]),1);self.assertFalse(self.dest.exists())
            job=client.call("/execute",dict(plan_id=plan["plan_id"],confirmed="CREATE_HARDLINKS"))
            for _ in range(200):
                result=client.call("/jobs/"+job["job_id"])
                if result["done"]:break
                time.sleep(.01)
            self.assertEqual(result["created"],1);self.assertEqual(result["error"],"")
        finally:server.shutdown();server.server_close();thread.join()
if __name__=="__main__":unittest.main()
