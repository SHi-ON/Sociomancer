import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "openai"))
import delete_chatgpt_library_files as cleanup


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.files = [
            cleanup.LibraryFile("lib1", "file1", "one.png", None, None, None),
            cleanup.LibraryFile("lib2", "file2", "two.png", None, None, None),
        ]

    def run_cleanup(self, args, delete_results=(True, True), trash_result=(3, 0)):
        with (
            patch.object(cleanup, "resolve_credentials", return_value=("token", None)),
            patch.object(cleanup, "list_library_files", return_value=self.files),
            patch.object(cleanup, "delete_library_file", side_effect=delete_results) as delete,
            patch.object(cleanup, "empty_file_trash", return_value=trash_result) as trash,
            patch.object(cleanup.time, "sleep") as sleep,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            result = cleanup.main(args)
            return result, delete.call_count, trash.call_count, sleep.call_args_list

    def test_successful_batch_empties_trash_once_after_delay(self):
        result, deleted, emptied, delays = self.run_cleanup(["--all", "--yes"])
        self.assertEqual((result, deleted, emptied), (0, 2, 1))
        self.assertEqual(delays, [call(cleanup.DELETE_SPACING), call(cleanup.TRASH_DELAY)])

    def test_dry_run_and_list_do_not_empty_trash(self):
        for args in (["--all", "--dry-run"], ["--list"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_cleanup(args)[:3], (0, 0, 0))

    def test_selected_file_deletion_also_empties_trash(self):
        result, deleted, emptied, delays = self.run_cleanup(
            ["--delete", "lib1", "--yes"]
        )
        self.assertEqual((result, deleted, emptied), (0, 1, 1))
        self.assertEqual(delays, [call(cleanup.TRASH_DELAY)])

    def test_failed_deletion_does_not_empty_trash(self):
        result, deleted, emptied, _ = self.run_cleanup(
            ["--all", "--yes"], delete_results=(True, False)
        )
        self.assertEqual((result, deleted, emptied), (1, 2, 0))

    def test_trash_partial_failure_sets_failure_status(self):
        result, deleted, emptied, _ = self.run_cleanup(
            ["--all", "--yes"], trash_result=(2, 1)
        )
        self.assertEqual((result, deleted, emptied), (1, 2, 1))

    def test_trash_response_must_contain_valid_counts(self):
        for response in ({}, {"deleted_count": 1, "failed_count": False}, None):
            with self.subTest(response=response):
                with patch.object(cleanup, "_request", return_value=response):
                    with self.assertRaises(cleanup.ChatGPTAPIError):
                        cleanup.empty_file_trash("token", None)

    def test_trash_request_uses_bulk_endpoint(self):
        with patch.object(
            cleanup, "_request", return_value={"deleted_count": 3, "failed_count": 0}
        ) as request:
            self.assertEqual(cleanup.empty_file_trash("token", None), (3, 0))
        request.assert_called_once_with(
            "DELETE", "/backend-api/files/library/trash", "token", None,
            cleanup.TRASH_TIMEOUT,
        )

    def test_timeout_does_not_claim_trash_was_emptied(self):
        with patch.object(cleanup, "urlopen", side_effect=TimeoutError):
            with self.assertRaisesRegex(cleanup.ChatGPTAPIError, "outcome is unknown"):
                cleanup.empty_file_trash("token", None)


if __name__ == "__main__":
    unittest.main()
