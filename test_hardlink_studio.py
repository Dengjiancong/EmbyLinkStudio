import stat
import unittest
from types import SimpleNamespace
from hardlink_studio import short_component, target_relative, overlap, Studio, excluded_path, checked, create_hardlink, require_hardlink, LinkSFTPClient

class FakeSFTP:
    def __init__(self):
        self.entries={'/':stat.S_IFDIR,'/source':stat.S_IFDIR,'/link':stat.S_IFLNK,'/file':stat.S_IFREG}
        self.created=[]
    def lstat(self,path):
        if path not in self.entries:raise FileNotFoundError(path)
        return SimpleNamespace(st_mode=self.entries[path])
    def mkdir(self,path):self.entries[path]=stat.S_IFDIR;self.created.append(path)

class SFTPLinkTests(unittest.TestCase):
    def test_extension_request_preserves_unicode_and_special_characters(self):
        from unittest.mock import Mock
        from paramiko.sftp import CMD_EXTENDED,CMD_STATUS
        sf=Mock();sf.extensions={'hardlink@openssh.com':'1'};sf._request.return_value=(CMD_STATUS,None)
        source="/源目录/很长的标题 ' $().mp4";dest='/入口/short.mp4'
        create_hardlink(sf,source,dest)
        sf._request.assert_called_once_with(CMD_EXTENDED,'hardlink@openssh.com',source,dest)
        sf.exec_command.assert_not_called()
    def test_unsupported_does_not_send_mutation(self):
        from unittest.mock import Mock
        sf=Mock();sf.extensions={}
        with self.assertRaisesRegex(RuntimeError,'未声明支持'):create_hardlink(sf,'/a','/b')
        sf._request.assert_not_called()
    def test_permission_error_has_target(self):
        from unittest.mock import Mock
        sf=Mock();sf.extensions={'hardlink@openssh.com':'1'};sf._request.side_effect=PermissionError(13,'Permission denied')
        with self.assertRaisesRegex(PermissionError,'/target'):create_hardlink(sf,'/source','/target')
    def test_handshake_parses_extension_version(self):
        from paramiko import Message
        from paramiko.sftp import CMD_VERSION
        from unittest.mock import Mock
        sf=LinkSFTPClient.__new__(LinkSFTPClient);sf._send_packet=Mock()
        m=Message();m.add_int(3);m.add_string('hardlink@openssh.com');m.add_string('1')
        sf._read_packet=lambda:(CMD_VERSION,m.asbytes())
        self.assertEqual(sf._send_version(),3);self.assertEqual(sf.extensions,{'hardlink@openssh.com':'1'})

class ErrorContextTests(unittest.TestCase):
    def test_permission_includes_operation_and_path(self):
        def denied():raise PermissionError(13, 'Permission denied')
        with self.assertRaises(PermissionError) as error:
            checked('检查 NAS 目录 /source/private', denied)
        self.assertIn('/source/private',str(error.exception))
        self.assertIn('权限不足',str(error.exception))
    def test_not_found_retains_type_for_optional_targets(self):
        def missing():raise FileNotFoundError(2, 'No such file')
        with self.assertRaises(FileNotFoundError):checked('检查目标',missing)
    def test_directory_permission_identifies_parent(self):
        class Denied(FakeSFTP):
            def lstat(self,path):raise PermissionError(13,'Permission denied')
        with self.assertRaisesRegex(PermissionError,'/private'):
            Studio.safe_path(Denied(),'/private/child')

class Tests(unittest.TestCase):
    def test_utf8_boundaries(self):
        for name in ['普通标题','中文'*100,'a'*200,'标题💖'*80]:
            self.assertLessEqual(len(short_component(name,80).encode('utf-8')),80)
    def test_paired_extensions(self):
        stem='中文💖'*100
        results=[target_relative('作者/'+stem+ext).rsplit('.',1)[0] for ext in ('.mp4','.jpg','.srt','.jpeg')]
        self.assertEqual(len(set(results)),1)
    def test_stable_mapping_and_different_names(self):
        a='a'*200
        self.assertEqual(target_relative(a+'.mp4'),target_relative(a+'.mp4'))
        self.assertNotEqual(target_relative(a+'.mp4'),target_relative(a+'b.mp4'))
    def test_nested_directory_limits(self):
        result=target_relative('/'.join(['目录'*100]*3+['标题'*100+'.mp4']))
        self.assertTrue(all(len(x.encode('utf-8'))<=80 for x in result.split('/')[:-1]))
    def test_overlap_boundaries(self):
        self.assertTrue(overlap('/data/a','/data/a/b'))
        self.assertTrue(overlap('/data/a/b','/data/a'))
        self.assertFalse(overlap('/data/a','/data/ab'))
    def test_readonly_does_not_create(self):
        sf=FakeSFTP();Studio.safe_path(sf,'/new/child');self.assertEqual(sf.created,[])
    def test_create_parents_only_when_requested(self):
        sf=FakeSFTP();Studio.safe_path(sf,'/new/child',True)
        self.assertEqual(sf.created,['/new','/new/child'])
    def test_reject_symlink_and_file_parents(self):
        for path in ('/link/child','/file/child'):
            with self.assertRaises(RuntimeError):Studio.safe_path(FakeSFTP(),path,True)

class RecycleTests(unittest.TestCase):
    def test_excludes_nested_and_case_variants(self):
        for path in ['.Trash-0/files/a.mp4', 'author/.Trash-1000/info/a.trashinfo',
                     '.Trash/files/a', '#recycle/a', '@Recycle/a', '$RECYCLE.BIN/a',
                     'author/.recycle/a', '.recyclebin/a', '/source/.Trash-0']:
            with self.subTest(path=path): self.assertTrue(excluded_path(path))
    def test_keeps_normal_and_hidden_media(self):
        for path in ['author/a.mp4', '.hidden/a.mp4', 'Trash.mp4', 'The Trash Movie/a.mp4', '.Trashcan/a']:
            with self.subTest(path=path): self.assertFalse(excluded_path(path))
    def test_scan_prunes_recycle_before_listing(self):
        import queue
        from unittest.mock import Mock
        class Remote(FakeSFTP):
            def __init__(self):
                super().__init__();self.visited=[]
            def stat(self,path):return self.lstat(path)
            def listdir_attr(self,path):
                self.visited.append(path)
                if path!='/source':raise AssertionError('Must not enumerate recycle children: '+path)
                return [SimpleNamespace(filename=n,st_mode=kind,st_size=10,st_mtime=1)
                        for n,kind in [('.Trash-0',stat.S_IFDIR),('.Trash-1000',stat.S_IFDIR),
                                       ('@Recycle',stat.S_IFDIR),('video.mp4',stat.S_IFREG),('video.jpg',stat.S_IFREG)]]
            def close(self):pass
        sf=Remote();client=Mock();app=Studio.__new__(Studio)
        app.q=queue.Queue();app.tree=Mock();app.tree.get_children.return_value=[];app.snapshot=lambda: {'source':'/source','dest':'/target'}
        app.connect=lambda c:(client,sf);app.start=lambda fn:fn()
        app.scan()
        events=list(app.q.queue)
        rows=next(data[0] for kind,data in events if kind=='rows')
        self.assertEqual({r['rel'] for r in rows},{'video.mp4','video.jpg'})
        self.assertEqual(sf.visited,['/source']);self.assertEqual(sf.created,[])
        client.exec_command.assert_not_called()
        self.assertTrue(any('排除回收站入口 3' in data for kind,data in events if kind=='status'))
    def test_execute_rejects_stale_recycle_preview(self):
        from unittest.mock import patch
        app=Studio.__new__(Studio)
        app.plan={'host':'nas','source':'/source','dest':'/target'}
        app.snapshot=lambda:dict(app.plan)
        app.rows=[dict(status='待创建',source='/source/.Trash-0/files/a.mp4',dest='/target/a.mp4')]
        with patch('hardlink_studio.messagebox.showerror') as err, patch('hardlink_studio.messagebox.askyesno') as confirm:
            app.execute();err.assert_called_once();confirm.assert_not_called()

if __name__=='__main__':unittest.main()
