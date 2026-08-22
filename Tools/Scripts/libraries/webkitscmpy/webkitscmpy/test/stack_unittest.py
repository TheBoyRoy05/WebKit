# Copyright (C) 2026 Apple Inc. All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1.  Redistributions of source code must retain the above copyright
#     notice, this list of conditions and the following disclaimer.
# 2.  Redistributions in binary form must reproduce the above copyright
#     notice, this list of conditions and the following disclaimer in the
#     documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY APPLE INC. AND ITS CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL APPLE INC. OR ITS CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import logging
import os
from argparse import Namespace
from unittest.mock import patch

from webkitbugspy import bugzilla, radar
from webkitbugspy import mocks as bmocks
from webkitcorepy import OutputCapture, testing
from webkitcorepy.mocks import Environment, Time as MockTime

from webkitscmpy import Commit, local, mocks, program

BUGZILLA = 'https://bugs.example.com'
CONTRIBUTOR = {'name': 'Tim Contributor', 'emails': ['tcontributor@example.com']}


class TestStack(testing.PathTestCase):
    basepath = 'mock/repository'

    def setUp(self):
        super().setUp()
        os.mkdir(os.path.join(self.path, '.git'))
        os.mkdir(os.path.join(self.path, '.svn'))

    @classmethod
    def add_parent(cls, repo):
        repo.commits['eng/parent'] = [
            repo.commits[repo.default_branch][-1],
            Commit(
                hash='06de5d56554e693db72313f4ca1fb969c30b8ccb',
                branch='eng/parent',
                author=CONTRIBUTOR,
                identifier='5.1@eng/parent',
                timestamp=1601668000,
                message='[Testing] Parent change\n',
            ),
        ]
        return repo

    @classmethod
    def add_stack(cls, repo):
        cls.add_parent(repo)
        repo.commits['eng/child'] = [
            repo.commits['eng/parent'][-1],
            Commit(
                hash='b8b921baaad2fd10bc9d0cc9e97f8fa1d6e5f4a1',
                branch='eng/child',
                author=CONTRIBUTOR,
                identifier='5.2@eng/child',
                timestamp=1601669000,
                message='[Testing] Child change\n',
            ),
        ]
        repo.head = repo.commits['eng/child'][-1]
        return repo

    @classmethod
    def record_into(cls, recorded):
        def capture(git, step):
            recorded.append(step)
            return 0
        return capture

    def test_set_parent(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            # Recording the same parent again is not an error
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

            config = local.Git(self.path).config()
            self.assertEqual(config.get('branch.eng/child.stack-parent'), 'eng/parent')
            self.assertEqual(
                config.get('branch.eng/child.stack-base'),
                repo.commits['eng/parent'][-1].hash,
            )

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(
            captured.stdout.getvalue(),
            "'eng/child' is stacked on 'eng/parent'\n" * 2,
        )

    def test_set_parent_missing(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(1, program.main(args=('stack', '--on', 'eng/missing'), path=self.path))

        self.assertEqual(
            captured.stderr.getvalue(),
            "Could not find 'eng/missing' as a branch, pull-request, or issue in this checkout\n",
        )

    def test_set_parent_production(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(1, program.main(args=('stack', '--on', 'main'), path=self.path))
            self.assertIsNone(local.Git(self.path).config().get('branch.eng/child.stack-parent'))

        self.assertEqual(
            captured.stderr.getvalue(),
            "'main' is not a development branch, a branch cannot be stacked on it\n",
        )

    def test_set_parent_self(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(1, program.main(args=('stack', '--on', 'eng/child'), path=self.path))

        self.assertEqual(captured.stderr.getvalue(), "'eng/child' cannot be stacked on itself\n")

    def test_set_parent_self_with_child(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

            # 'eng/parent' already has 'eng/child' stacked on it, which must not be reported
            # in place of the branch being stacked on itself
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(1, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

        self.assertEqual(
            captured.stderr.getvalue(),
            "'eng/parent' cannot be stacked on itself\n",
        )

    def test_set_parent_cycle(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(1, program.main(args=('stack', '--on', 'eng/child'), path=self.path))

        self.assertEqual(
            captured.stderr.getvalue(),
            "'eng/child' is stacked on 'eng/parent,' stacking 'eng/parent' on it would create a cycle\n",
        )

    def test_set_parent_refuses_a_cycle_above(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_parent(repo)
            repo.commits['eng/child'] = [
                repo.commits['eng/parent'][-1],
                Commit(
                    hash='b8b921baaad2fd10bc9d0cc9e97f8fa1d6e5f4a1',
                    branch='eng/child',
                    author=CONTRIBUTOR,
                    identifier='5.2@eng/child',
                    timestamp=1601669000,
                    message='[Testing] Child change\n',
                ),
            ]
            repo.commits['eng/other'] = [
                repo.commits[repo.default_branch][-1],
                Commit(
                    hash='9d1a3f6c2b8e4d5a7c0f1e2b3a4d5c6e7f809123',
                    branch='eng/other',
                    author=CONTRIBUTOR,
                    identifier='5.1@eng/other',
                    timestamp=1601669500,
                    message='[Testing] Other change\n',
                ),
            ]
            repo.head = repo.commits['eng/parent'][-1]

            # Hand-written config where two branches are stacked on each other. The walk up
            # from 'eng/parent' finds the cycle before the walk down from it ever runs.
            repo.edit_config('branch.eng/child.stack-parent', 'eng/parent')
            repo.edit_config('branch.eng/parent.stack-parent', 'eng/child')

            self.assertEqual(1, program.main(args=('stack', '--on', 'eng/other'), path=self.path))

        self.assertIn('is part of a cycle of stacked branches\n', captured.stderr.getvalue())

    def test_set_parent_on_pull_request(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('pull-request', '-v', '--no-history'), path=self.path))

            repo.head = repo.commits['eng/child'][-1]
            self.assertEqual(0, program.main(args=('stack', '--on', '1'), path=self.path))
            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/parent',
            )

        self.assertIn("'eng/child' is stacked on 'eng/parent'", captured.stdout.getvalue())

    def test_set_parent_rebases_when_asked(self):
        with OutputCapture(level=logging.INFO) as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            landed = Commit(
                hash='c37b6b6ba1c99f2b4c6b53c1e1a1e0c9f3f2bd3d',
                branch='main',
                author=CONTRIBUTOR,
                identifier='6@main',
                timestamp=1601670000,
                message='[Testing] Landed on main\n',
            )
            repo.commits['main'].append(landed)
            repo.remotes['origin/main'] = repo.commits['main'][:]

            # '--rebase' alongside '--on' used to be dropped on the floor
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent', '--rebase', '-v'), path=self.path))
            self.assertEqual(repo.commits['eng/parent'][0].hash, landed.hash)

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertIn("Rebasing 'eng/child' on 'remotes/origin/main'...", captured.root.log.getvalue())

    def test_set_parent_on_ambiguous_issue(self):
        with OutputCapture(level=logging.WARNING) as captured, mocks.local.Git(self.path) as repo, \
                mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            repo.commits['eng/older'] = repo.commits['eng/parent'][:]

            # The same issue was worked on twice, so the argument does not name one branch
            repo.edit_config('branch.eng/parent.bug', 'https://bugs.webkit.org/show_bug.cgi?id=321654')
            repo.edit_config('branch.eng/older.bug', 'https://bugs.webkit.org/show_bug.cgi?id=321654')

            self.assertEqual(0, program.main(args=('stack', '--on', '321654'), path=self.path))
            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/older',
            )

        self.assertIn(
            "'321654' matches eng/older, eng/parent, stacking on 'eng/older'",
            captured.root.log.getvalue(),
        )

    def test_set_parent_on_unstackable_pull_request(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('pull-request', '-v', '--no-history'), path=self.path))
            github.pull_requests[0]['merged'] = True

            # '1' matches an issue as well, which must not be used once the pull-request has refused
            repo.edit_config('branch.eng/parent.bug', 'https://bugs.webkit.org/show_bug.cgi?id=1')

            repo.head = repo.commits['eng/child'][-1]
            self.assertEqual(1, program.main(args=('stack', '--on', '1'), path=self.path))
            self.assertIsNone(local.Git(self.path).config().get('branch.eng/child.stack-parent'))

            # A pull-request whose branch was never fetched must refuse the same way
            github.pull_requests[0]['merged'] = False
            github.pull_requests[0]['head']['ref'] = 'eng/never-checked-out'
            self.assertEqual(1, program.main(args=('stack', '--on', '1'), path=self.path))
            self.assertIsNone(local.Git(self.path).config().get('branch.eng/child.stack-parent'))

        self.assertIn('has already been merged, there is nothing to stack on', captured.stderr.getvalue())
        self.assertIn("is from 'eng/never-checked-out,' which does not exist", captured.stderr.getvalue())

    def test_set_parent_branching(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            repo.commits['eng/sibling'] = [
                repo.commits['eng/parent'][-1],
                Commit(
                    hash='2f0c1cbb7e5b6f2a3ba0a4c1e0f79c1c7d4a3b12',
                    branch='eng/sibling',
                    author=CONTRIBUTOR,
                    identifier='5.2@eng/sibling',
                    timestamp=1601669500,
                    message='[Testing] Sibling change\n',
                ),
            ]
            repo.head = repo.commits['eng/sibling'][-1]

            # Two branches may be stacked on the same branch
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            self.assertEqual(0, program.main(args=('stack',), path=self.path))

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(
            captured.stdout.getvalue(),
            "'eng/child' is stacked on 'eng/parent'\n"
            "'eng/sibling' is stacked on 'eng/parent'\n"
            'Stacked pull requests, bottom of the stack first:\n'
            '- eng/parent\n'
            '    - eng/child\n'
            '    - eng/sibling (this pull request)\n',
        )

    def test_unstack(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            self.assertEqual(0, program.main(args=('stack', '--unstack'), path=self.path))

            config = local.Git(self.path).config()
            self.assertIsNone(config.get('branch.eng/child.stack-parent'))
            self.assertIsNone(config.get('branch.eng/child.stack-base'))

            # Nothing sits beneath 'eng/child' any more, so it is replayed onto the production branch
            self.assertEqual(repo.commits['eng/child'][0].hash, repo.commits['main'][-1].hash)

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(
            captured.stdout.getvalue(),
            "'eng/child' is stacked on 'eng/parent'\n"
            "'eng/child' is no longer stacked on another branch\n",
        )

    def test_unstack_unstacked_branch_is_left_alone(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            before = repo.commits['eng/child'][0].hash

            # 'eng/child' was never stacked, so unstacking it must not move it
            self.assertEqual(0, program.main(args=('stack', '--unstack'), path=self.path))
            self.assertEqual(repo.commits['eng/child'][0].hash, before)

        self.assertEqual(captured.stderr.getvalue(), '')

    def test_parent_deleted(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            del repo.commits['eng/parent']
            self.assertEqual(0, program.main(args=('stack',), path=self.path))

        self.assertEqual(
            captured.stdout.getvalue(),
            "'eng/child' is stacked on 'eng/parent'\n"
            "'eng/child' is not part of a stack\n",
        )

    def test_listing(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            self.assertEqual(0, program.main(args=('stack',), path=self.path))

            # The whole stack is listed from either end, only the marked branch moves
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('stack',), path=self.path))

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(
            captured.stdout.getvalue(),
            "'eng/child' is stacked on 'eng/parent'\n"
            'Stacked pull requests, bottom of the stack first:\n'
            '- eng/parent\n'
            '    - eng/child (this pull request)\n'
            'Stacked pull requests, bottom of the stack first:\n'
            '- eng/parent (this pull request)\n'
            '    - eng/child\n',
        )

    def test_listing_not_stacked(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_parent(repo)
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('stack',), path=self.path))

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(captured.stdout.getvalue(), "'eng/parent' is not part of a stack\n")

    def test_listing_nests_a_deeper_sibling(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            repo.commits['eng/grandchild'] = [
                repo.commits['eng/child'][-1],
                Commit(
                    hash='7c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f60718293',
                    branch='eng/grandchild',
                    author=CONTRIBUTOR,
                    identifier='5.3@eng/grandchild',
                    timestamp=1601670000,
                    message='[Testing] Grandchild change\n',
                ),
            ]
            repo.commits['eng/sibling'] = [
                repo.commits['eng/parent'][-1],
                Commit(
                    hash='2f0c1cbb7e5b6f2a3ba0a4c1e0f79c1c7d4a3b12',
                    branch='eng/sibling',
                    author=CONTRIBUTOR,
                    identifier='5.2@eng/sibling',
                    timestamp=1601669500,
                    message='[Testing] Sibling change\n',
                ),
            ]
            repo.edit_config('branch.eng/child.stack-parent', 'eng/parent')
            repo.edit_config('branch.eng/grandchild.stack-parent', 'eng/child')
            repo.edit_config('branch.eng/sibling.stack-parent', 'eng/parent')
            repo.head = repo.commits['eng/parent'][-1]

            self.assertEqual(0, program.main(args=('stack',), path=self.path))

        # 'eng/grandchild' is deeper than 'eng/sibling', so it has to follow the branch it is
        # stacked on rather than whichever branch was listed immediately before it
        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(
            captured.stdout.getvalue(),
            'Stacked pull requests, bottom of the stack first:\n'
            '- eng/parent (this pull request)\n'
            '    - eng/child\n'
            '        - eng/grandchild\n'
            '    - eng/sibling\n',
        )

    def test_listing_refuses_a_cycle_above(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            # Hand-written config can stack two branches on each other
            repo.edit_config('branch.eng/child.stack-parent', 'eng/parent')
            repo.edit_config('branch.eng/parent.stack-parent', 'eng/child')

            self.assertEqual(1, program.main(args=('stack',), path=self.path))

        self.assertIn('is part of a cycle of stacked branches\n', captured.stderr.getvalue())

    def test_rebase(self):
        with OutputCapture(level=logging.INFO) as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

            landed = Commit(
                hash='c37b6b6ba1c99f2b4c6b53c1e1a1e0c9f3f2bd3d',
                branch='main',
                author=CONTRIBUTOR,
                identifier='6@main',
                timestamp=1601670000,
                message='[Testing] Landed on main\n',
            )
            repo.commits['main'].append(landed)
            repo.remotes['origin/main'] = repo.commits['main'][:]

            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('stack', '--rebase', '-v'), path=self.path))
            self.assertEqual(repo.commits['eng/parent'][0].hash, landed.hash)
            self.assertEqual(repo.commits['eng/child'][0].hash, repo.commits['eng/parent'][-1].hash)

            # The run ends on top of the stack, so the cascade has to come back
            self.assertEqual(repo.head.branch, 'eng/parent')

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(
            [line for line in captured.root.log.getvalue().splitlines() if 'Mock process' not in line],
            ["Rebasing 'eng/child' on 'remotes/origin/main'..."],
        )

    def test_rebase_falls_back_when_git_cannot_update_refs(self):
        recorded = []

        with OutputCapture(level=logging.INFO), mocks.local.Git(self.path, git_version='2.37.0') as repo, \
                mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

            with patch.object(program.Stack, '_rebase_onto', self.record_into(recorded)):
                self.assertEqual(0, program.Stack.rebase(local.Git(self.path)))

        # Without '--update-refs' every branch has to be replayed on its own
        self.assertEqual(
            [(branch, update_refs) for branch, _, _, update_refs in recorded],
            [('eng/parent', False), ('eng/child', False)],
        )

    def test_rebase_records_missing_base(self):
        with OutputCapture(level=logging.INFO), mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

            # A stack recorded before stack-base existed, or one whose base was hand-removed
            repo.edit_config('branch.eng/child.stack-base', None)
            self.assertIsNone(local.Git(self.path).config().get('branch.eng/child.stack-base'))

            self.assertEqual(0, program.main(args=('stack', '--rebase'), path=self.path))

            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-base'),
                repo.commits['eng/parent'][-1].hash,
            )

    def test_rebase_uses_recorded_base(self):
        recorded = []

        with OutputCapture(level=logging.INFO), mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            base = local.Git(self.path).config().get('branch.eng/child.stack-base')

            # Amending the parent rewrites its tip, so the child no longer descends from it
            repo.commits['eng/parent'][-1] = Commit(
                hash='9a1c0e5f4b7d2a8e3c6b1f0d9e8a7c5b4d3e2f10',
                branch='eng/parent',
                author=CONTRIBUTOR,
                identifier='5.1@eng/parent',
                timestamp=1601668500,
                message='[Testing] Parent change, amended\n',
            )

            with patch.object(program.Stack, '_rebase_onto', self.record_into(recorded)):
                self.assertEqual(0, program.Stack.rebase(local.Git(self.path)))

            # The child replays from the parent's tip as of the last cascade rather than being swept
            # into the parent's rebase, which is what lets an amended parent be recovered from
            self.assertEqual(recorded[-1], ('eng/child', 'eng/parent', base, False))

    def test_rebase_branches_from_the_fork_point(self):
        recorded = []

        with OutputCapture(level=logging.INFO), mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            repo.commits['eng/sibling'] = [
                repo.commits['eng/parent'][-1],
                Commit(
                    hash='2f0c1cbb7e5b6f2a3ba0a4c1e0f79c1c7d4a3b12',
                    branch='eng/sibling',
                    author=CONTRIBUTOR,
                    identifier='5.2@eng/sibling',
                    timestamp=1601669500,
                    message='[Testing] Sibling change\n',
                ),
            ]
            repo.head = repo.commits['eng/sibling'][-1]
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            fork = local.Git(self.path).config().get('branch.eng/sibling.stack-base')

            repo.commits['eng/nephew'] = [
                repo.commits['eng/sibling'][-1],
                Commit(
                    hash='7d1e4a9c0b5f3e8a2d6c4b1f9e0a8c7b5d3f2e14',
                    branch='eng/nephew',
                    author=CONTRIBUTOR,
                    identifier='5.3@eng/nephew',
                    timestamp=1601669800,
                    message='[Testing] Nephew change\n',
                ),
            ]
            repo.head = repo.commits['eng/nephew'][-1]
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/sibling'), path=self.path))

            repo.head = repo.commits['eng/child'][-1]
            with patch.object(program.Stack, '_rebase_onto', self.record_into(recorded)):
                self.assertEqual(0, program.Stack.rebase(local.Git(self.path)))

            # The first leaf carries the shared parent with it, so the second subtree replays from
            # where it forked rather than sweeping the parent in a second time, and then batches
            self.assertEqual(recorded, [
                ('eng/child', 'remotes/origin/main', repo.commits['main'][-1].hash, True),
                ('eng/nephew', 'eng/parent', fork, True),
            ])

            repo.remotes['origin/main'] = repo.commits['main'][:]
            self.assertEqual(0, program.Stack.rebase(local.Git(self.path)))

            # The cascade ends on top of the last run, so it has to come back
            self.assertEqual(repo.head.branch, 'eng/child')

    def test_rebase_refuses_detached_head(self):
        with OutputCapture() as captured, mocks.local.Git(self.path, detached=True) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(1, program.main(args=('stack', '--rebase'), path=self.path))

        self.assertEqual(
            captured.stderr.getvalue(),
            'HEAD is not on a branch, so there is no stack to rebase\n',
        )

    def test_pull_cascades_stack(self):
        with OutputCapture(level=logging.INFO) as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            self.assertEqual(0, program.main(args=('pull', '-v'), path=self.path))

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(
            [line for line in captured.root.log.getvalue().splitlines() if 'Mock process' not in line],
            ["Rebasing 'eng/child' on 'remotes/origin/main'..."],
        )

    def test_pull_unstacked_branch_unchanged(self):
        with OutputCapture(level=logging.INFO) as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_parent(repo)
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('pull', '-v'), path=self.path))

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertNotIn('Rebasing', captured.root.log.getvalue())

    def test_restack_records_base_when_already_stacked(self):
        recorded = []

        with OutputCapture(), mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            repository = local.Git(self.path)
            repo.edit_config('branch.eng/child.stack-parent', 'eng/parent')

            with patch.object(program.Stack, '_rebase_onto', self.record_into(recorded)):
                self.assertEqual(0, program.Stack._restack(repository, 'eng/child', 'eng/parent'))

            # 'eng/child' already sits on 'eng/parent,' so there is nothing to replay
            self.assertEqual(recorded, [])
            self.assertEqual(
                repository.config(cached=False).get('branch.eng/child.stack-base'),
                repo.commits['eng/parent'][-1].hash,
            )

    def test_restack_uses_recorded_base(self):
        recorded = []

        with OutputCapture(), mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_parent(repo)
            # 'eng/sibling' does not descend from 'eng/parent', so restack has to replay it
            repo.commits['eng/sibling'] = [
                repo.commits[repo.default_branch][-1],
                Commit(
                    hash='2f0c1cbb7e5b6f2a3ba0a4c1e0f79c1c7d4a3b12',
                    branch='eng/sibling',
                    author=CONTRIBUTOR,
                    identifier='5.1@eng/sibling',
                    timestamp=1601669500,
                    message='[Testing] Sibling change\n',
                ),
            ]
            repo.head = repo.commits['eng/sibling'][-1]
            repo.edit_config('branch.eng/sibling.stack-parent', 'eng/parent')
            repo.edit_config('branch.eng/sibling.stack-base', 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef')

            with patch.object(program.Stack, '_rebase_onto', self.record_into(recorded)):
                self.assertEqual(0, program.Stack._restack(local.Git(self.path), 'eng/sibling', 'eng/parent'))

            # Replaying leaves 'eng/sibling' on the parent's tip, so that becomes its new base
            self.assertEqual(
                local.Git(self.path).config(cached=False).get('branch.eng/sibling.stack-base'),
                repo.commits['eng/parent'][-1].hash,
            )

        self.assertEqual(recorded, [
            ('eng/sibling', 'eng/parent', 'deadbeefdeadbeefdeadbeefdeadbeefdeadbeef', False),
        ])

    def test_branch_on(self):
        with OutputCapture(level=logging.INFO) as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_parent(repo)
            self.assertEqual(0, program.main(
                args=('branch', '-i', 'child', '-v', '--on', 'eng/parent'),
                path=self.path,
            ))
            self.assertEqual(repo.branch, 'eng/child')
            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/parent',
            )
            self.assertEqual(repo.commits['eng/child'][-1].hash, repo.commits['eng/parent'][-1].hash)

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(
            captured.stdout.getvalue(),
            "Created the local development branch 'eng/child' stacked on 'eng/parent'\n",
        )

    def test_branch_on_shorthand(self):
        with OutputCapture(level=logging.INFO) as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_parent(repo)
            self.assertEqual(0, program.main(
                args=('branch', '-i', 'child', '-v', '--on', 'parent'),
                path=self.path,
            ))
            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/parent',
            )

        self.assertEqual(captured.stderr.getvalue(), '')

    def test_branch_on_missing(self):
        with OutputCapture() as captured, mocks.local.Git(self.path), mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.assertEqual(1, program.main(
                args=('branch', '-i', 'child', '--on', 'eng/missing'),
                path=self.path,
            ))

        self.assertEqual(
            captured.stderr.getvalue(),
            "Could not find 'eng/missing' as a branch, pull-request, or issue in this checkout\n",
        )
        self.assertEqual(captured.stdout.getvalue(), '')

    def test_branch_on_production(self):
        with OutputCapture() as captured, mocks.local.Git(self.path), mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.assertEqual(1, program.main(
                args=('branch', '-i', 'child', '--on', 'main'),
                path=self.path,
            ))

        self.assertEqual(
            captured.stderr.getvalue(),
            "'main' is not a development branch, a branch cannot be stacked on it\n",
        )

    def test_branch_on_existing(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            repo.head = repo.commits['eng/parent'][-1]

            # 'eng/child' already exists, so stack it rather than asking for a second command
            self.assertEqual(0, program.main(
                args=('branch', '-i', 'child', '--on', 'eng/parent'),
                path=self.path,
            ))
            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/parent',
            )
            self.assertEqual(repo.branch, 'eng/child')

        self.assertEqual(captured.stderr.getvalue(), '')
        self.assertEqual(
            captured.stdout.getvalue(),
            "Stacked the local development branch 'eng/child' on 'eng/parent'\n",
        )

    def test_branch_on_pull_request(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_parent(repo)
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('pull-request', '-v', '--no-history'), path=self.path))

            repo.head = repo.commits[repo.default_branch][-1]
            self.assertEqual(0, program.main(
                args=('branch', '-i', 'child', '--on', '1'),
                path=self.path,
            ))

            # The pull-request resolves to the branch it is from
            self.assertEqual(repo.branch, 'eng/child')
            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/parent',
            )
            self.assertEqual(repo.commits['eng/child'][-1].hash, repo.commits['eng/parent'][-1].hash)

        self.assertIn(
            "Created the local development branch 'eng/child' stacked on 'eng/parent'",
            captured.stdout.getvalue(),
        )

    def test_pull_request(self):
        with OutputCapture(level=logging.INFO) as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))
            self.assertEqual(0, program.main(
                args=('pull-request', '-v', '--no-history'),
                path=self.path,
            ))

            created = local.Git(self.path).remote().pull_requests.get(1)
            self.assertEqual(created.base, 'main')
            self.assertEqual(created.head, 'eng/child')
            self.assertEqual([commit.hash for commit in created.commits], [repo.commits['eng/child'][-1].hash])
            self.assertEqual(
                created.body,
                'Stacked pull requests, bottom of the stack first:\n'
                '- eng/parent (not uploaded)\n'
                '    - eng/child (this pull request)',
            )

    def test_pull_request_number_parsing(self):
        self.assertEqual(program.Stack.pull_request_number('71333'), 71333)
        self.assertEqual(program.Stack.pull_request_number('https://github.com/WebKit/WebKit/pull/71333'), 71333)
        self.assertEqual(program.Stack.pull_request_number('https://github.com/WebKit/WebKit/pull/71333/files'), 71333)
        self.assertIsNone(program.Stack.pull_request_number('rdar://184066679'))
        self.assertIsNone(program.Stack.pull_request_number('https://bugs.webkit.org/show_bug.cgi?id=320031'))
        self.assertIsNone(program.Stack.pull_request_number('eng/parent'))

    def test_pull_request_stacked_on(self):
        with OutputCapture(level=logging.INFO), mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('pull-request', '-v', '--no-history'), path=self.path))

            repo.head = repo.commits['eng/child'][-1]
            self.assertEqual(0, program.main(
                args=('pull-request', '-v', '--no-history', '--stacked-on', '1'),
                path=self.path,
            ))

            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/parent',
            )
            child = local.Git(self.path).remote().pull_requests.get(2)
            self.assertEqual(child.base, 'main')
            self.assertEqual([commit.hash for commit in child.commits], [repo.commits['eng/child'][-1].hash])
            self.assertEqual(
                child.body,
                'Stacked pull requests, bottom of the stack first:\n'
                '- #1 eng/parent\n'
                '    - eng/child (this pull request)',
            )

    def test_pull_request_stacked_on_url(self):
        with OutputCapture(level=logging.INFO), mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('pull-request', '-v', '--no-history'), path=self.path))

            repo.head = repo.commits['eng/child'][-1]
            self.assertEqual(0, program.main(
                args=(
                    'pull-request', '-v', '--no-history',
                    '--stacked-on', f'https://{github.hosts[0]}/WebKit/WebKit/pull/1',
                ),
                path=self.path,
            ))
            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/parent',
            )

    def test_pull_request_stacked_on_branch_name(self):
        with OutputCapture(level=logging.INFO), mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            self.assertEqual(0, program.main(
                args=('pull-request', '-v', '--no-history', '--on', 'eng/parent'),
                path=self.path,
            ))

            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/parent',
            )
            created = local.Git(self.path).remote().pull_requests.get(1)
            self.assertEqual(created.base, 'main')
            self.assertEqual([commit.hash for commit in created.commits], [repo.commits['eng/child'][-1].hash])

    def test_pull_request_stacked_on_issue(self):
        with OutputCapture(level=logging.INFO), mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            repo.edit_config('branch.eng/parent.bug', 'https://bugs.webkit.org/show_bug.cgi?id=321654')

            # A number nothing else claims is looked up against the issue each branch records
            self.assertEqual(0, program.main(
                args=('pull-request', '-v', '--no-history', '--on', '321654'),
                path=self.path,
            ))
            self.assertEqual(
                local.Git(self.path).config().get('branch.eng/child.stack-parent'),
                'eng/parent',
            )

    def test_pull_request_stacked_on_unknown(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            self.assertEqual(1, program.main(
                args=('pull-request', '--no-history', '--stacked-on', 'rdar://184066679'),
                path=self.path,
            ))

        self.assertIn(
            "Could not find 'rdar://184066679' as a branch, pull-request, or issue in this checkout\n",
            captured.stderr.getvalue(),
        )

    def test_pull_request_stacked_on_missing_branch(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('pull-request', '-v', '--no-history'), path=self.path))
            github.pull_requests[0]['head']['ref'] = 'eng/never-checked-out'

            repo.head = repo.commits['eng/child'][-1]
            self.assertEqual(1, program.main(
                args=('pull-request', '--no-history', '--stacked-on', '1'),
                path=self.path,
            ))
            self.assertIsNone(local.Git(self.path).config().get('branch.eng/child.stack-parent'))

        self.assertIn(
            "is from 'eng/never-checked-out,' which does not exist in this checkout\n",
            captured.stderr.getvalue(),
        )
        self.assertIn("Fetch that branch before stacking 'eng/child' on it\n", captured.stderr.getvalue())

    def test_pull_request_stacked_on_merged(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            repo.head = repo.commits['eng/parent'][-1]
            self.assertEqual(0, program.main(args=('pull-request', '-v', '--no-history'), path=self.path))
            github.pull_requests[0]['merged'] = True

            repo.head = repo.commits['eng/child'][-1]
            self.assertEqual(1, program.main(
                args=('pull-request', '--no-history', '--stacked-on', '1'),
                path=self.path,
            ))
            self.assertIsNone(local.Git(self.path).config().get('branch.eng/child.stack-parent'))

        self.assertIn('has already been merged, there is nothing to stack on\n', captured.stderr.getvalue())

    def test_rebase_conflict_explains_how_to_resume(self):
        def fail(git, step):
            return 1

        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

            with patch.object(program.Stack, '_rebase_onto', fail):
                self.assertEqual(1, program.main(args=('stack', '--rebase'), path=self.path))

        self.assertIn("stack --rebase' to replay the rest of the stack", captured.stderr.getvalue())

    def test_restack_conflict_explains_how_to_resume(self):
        def fail(git, step):
            return 1

        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), \
                patch('webkitbugspy.Tracker._trackers', []), MockTime:
            self.add_parent(repo)
            repo.commits['eng/sibling'] = [
                repo.commits[repo.default_branch][-1],
                Commit(
                    hash='2f0c1cbb7e5b6f2a3ba0a4c1e0f79c1c7d4a3b12',
                    branch='eng/sibling',
                    author=CONTRIBUTOR,
                    identifier='5.1@eng/sibling',
                    timestamp=1601669500,
                    message='[Testing] Sibling change\n',
                ),
            ]
            repo.head = repo.commits['eng/sibling'][-1]

            # A single branch is being replayed, so there is no rest of the stack to mention
            with patch.object(program.Stack, '_rebase_onto', fail):
                self.assertEqual(1, program.Stack._restack(local.Git(self.path), 'eng/sibling', 'eng/parent'))

        self.assertIn("to finish stacking 'eng/sibling' on 'eng/parent'", captured.stderr.getvalue())
        self.assertNotIn('rest of the stack', captured.stderr.getvalue())

    def test_re_parenting_forgets_the_old_dependency(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), bmocks.Bugzilla(
            BUGZILLA.split('://')[-1],
            issues=bmocks.ISSUES,
            environment=Environment(
                BUGS_EXAMPLE_COM_USERNAME='tcontributor@example.com',
                BUGS_EXAMPLE_COM_PASSWORD='password',
            ),
        ), patch('webkitbugspy.Tracker._trackers', [bugzilla.Tracker(BUGZILLA)]), MockTime:
            self.add_stack_with_bugs(repo)
            repo.commits['eng/sibling'] = [
                repo.commits[repo.default_branch][-1],
                Commit(
                    hash='2f0c1cbb7e5b6f2a3ba0a4c1e0f79c1c7d4a3b12',
                    branch='eng/sibling',
                    author=CONTRIBUTOR,
                    identifier='5.1@eng/sibling',
                    timestamp=1601669500,
                    message=f'[Testing] Sibling change\n{BUGZILLA}/show_bug.cgi?id=3\n',
                ),
            ]
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

            tracker = bugzilla.Tracker(BUGZILLA)
            tracker.issue(2).relate(depends_on=tracker.issue(1))
            self.assertEqual([issue.id for issue in tracker.issue(2).related['depends_on']], [1])

            args = Namespace(**{'_new_parent': 'eng/sibling'})
            self.assertEqual(
                program.PullRequest.ensure_stack_parent(args, local.Git(self.path)),
                ('eng/sibling', 0),
            )

            # The dependency on the branch it is no longer stacked on is dropped, from both ends
            self.assertEqual([issue.id for issue in bugzilla.Tracker(BUGZILLA).issue(2).related['depends_on']], [])
            self.assertEqual([issue.id for issue in bugzilla.Tracker(BUGZILLA).issue(1).related['blocks']], [])

        self.assertIn(
            "'eng/child' was stacked on 'eng/parent', stacking it on 'eng/sibling' instead",
            captured.stdout.getvalue(),
        )
        self.assertIn('no longer depends on', captured.stdout.getvalue())

    def test_unstack_forgets_the_dependency(self):
        with OutputCapture() as captured, mocks.local.Git(self.path) as repo, mocks.local.Svn(), bmocks.Bugzilla(
            BUGZILLA.split('://')[-1],
            issues=bmocks.ISSUES,
            environment=Environment(
                BUGS_EXAMPLE_COM_USERNAME='tcontributor@example.com',
                BUGS_EXAMPLE_COM_PASSWORD='password',
            ),
        ), patch('webkitbugspy.Tracker._trackers', [bugzilla.Tracker(BUGZILLA)]), MockTime:
            self.add_stack_with_bugs(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

            tracker = bugzilla.Tracker(BUGZILLA)
            tracker.issue(2).relate(depends_on=tracker.issue(1))

            self.assertEqual(0, program.main(args=('stack', '--unstack'), path=self.path))
            self.assertEqual([issue.id for issue in bugzilla.Tracker(BUGZILLA).issue(2).related['depends_on']], [])

        self.assertIn('no longer depends on', captured.stdout.getvalue())

    def test_pull_request_no_rebase_detects_broken_stack(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), patch('webkitbugspy.Tracker._trackers', []):
            self.add_stack(repo)
            self.assertEqual(0, program.main(args=('stack', '--on', 'eng/parent'), path=self.path))

            # Put 'eng/child' straight onto main, leaving 'eng/parent' behind, as a
            # stack-unaware rebase of the child would
            repo.commits['eng/child'] = [
                repo.commits[repo.default_branch][-1],
                Commit(
                    hash='b8b921baaad2fd10bc9d0cc9e97f8fa1d6e5f4a1',
                    branch='eng/child',
                    author=CONTRIBUTOR,
                    identifier='5.1@eng/child',
                    timestamp=1601669000,
                    message='[Testing] Child change\n',
                ),
            ]
            repo.head = repo.commits['eng/child'][-1]

            self.assertEqual(1, program.main(
                args=('pull-request', '--no-history', '--no-rebase'),
                path=self.path,
            ))

        lines = captured.stderr.getvalue().splitlines()
        self.assertEqual(lines[0], "'eng/child' no longer sits on top of 'eng/parent'")
        self.assertIn("stack --rebase' or '", lines[1])
        self.assertIn("pull-request --rebase' to replay it", lines[1])

    @classmethod
    def add_stack_with_bugs(cls, repo):
        repo.commits['eng/parent'] = [
            repo.commits[repo.default_branch][-1],
            Commit(
                hash='06de5d56554e693db72313f4ca1fb969c30b8ccb',
                branch='eng/parent',
                author=CONTRIBUTOR,
                identifier='5.1@eng/parent',
                timestamp=1601668000,
                message=f'[Testing] Parent change\n{BUGZILLA}/show_bug.cgi?id=1\n',
            ),
        ]
        repo.commits['eng/child'] = [
            repo.commits['eng/parent'][-1],
            Commit(
                hash='b8b921baaad2fd10bc9d0cc9e97f8fa1d6e5f4a1',
                branch='eng/child',
                author=CONTRIBUTOR,
                identifier='5.2@eng/child',
                timestamp=1601669000,
                message=f'[Testing] Child change\n{BUGZILLA}/show_bug.cgi?id=2\n',
            ),
        ]
        repo.head = repo.commits['eng/child'][-1]
        return repo

    @classmethod
    def add_stack_with_radars(cls, repo):
        repo.commits['eng/parent'] = [
            repo.commits[repo.default_branch][-1],
            Commit(
                hash='06de5d56554e693db72313f4ca1fb969c30b8ccb',
                branch='eng/parent',
                author=CONTRIBUTOR,
                identifier='5.1@eng/parent',
                timestamp=1601668000,
                message='[Testing] Parent change\nrdar://1\n',
            ),
        ]
        repo.commits['eng/child'] = [
            repo.commits['eng/parent'][-1],
            Commit(
                hash='b8b921baaad2fd10bc9d0cc9e97f8fa1d6e5f4a1',
                branch='eng/child',
                author=CONTRIBUTOR,
                identifier='5.2@eng/child',
                timestamp=1601669000,
                message='[Testing] Child change\nrdar://2\n',
            ),
        ]
        repo.head = repo.commits['eng/child'][-1]
        return repo

    def test_pull_request_relates_issues(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), bmocks.Bugzilla(
            BUGZILLA.split('://')[-1],
            issues=bmocks.ISSUES,
            environment=Environment(
                BUGS_EXAMPLE_COM_USERNAME='tcontributor@example.com',
                BUGS_EXAMPLE_COM_PASSWORD='password',
            ),
        ), patch('webkitbugspy.Tracker._trackers', [bugzilla.Tracker(BUGZILLA)]):
            self.add_stack_with_bugs(repo)
            self.assertEqual(0, program.main(
                args=('pull-request', '--no-history', '--on', 'eng/parent'),
                path=self.path,
            ))

            child_issue = bugzilla.Tracker(BUGZILLA).issue(2)
            self.assertEqual(
                [issue.id for issue in child_issue.related['depends_on']],
                [1],
            )

        self.assertIn(
            f'{BUGZILLA}/show_bug.cgi?id=2 depends on {BUGZILLA}/show_bug.cgi?id=1',
            captured.stdout.getvalue(),
        )

    def test_pull_request_no_issue_skips_relating(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), bmocks.Bugzilla(
            BUGZILLA.split('://')[-1],
            issues=bmocks.ISSUES,
            environment=Environment(
                BUGS_EXAMPLE_COM_USERNAME='tcontributor@example.com',
                BUGS_EXAMPLE_COM_PASSWORD='password',
            ),
        ), patch('webkitbugspy.Tracker._trackers', [bugzilla.Tracker(BUGZILLA)]):
            self.add_stack_with_bugs(repo)
            self.assertEqual(0, program.main(
                args=('pull-request', '--no-history', '--on', 'eng/parent', '--no-issue'),
                path=self.path,
            ))

            self.assertEqual(bugzilla.Tracker(BUGZILLA).issue(2).related['depends_on'], [])

        self.assertNotIn('depends on', captured.stdout.getvalue())

    def test_pull_request_relates_issues_once(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), bmocks.Bugzilla(
            BUGZILLA.split('://')[-1],
            issues=bmocks.ISSUES,
            environment=Environment(
                BUGS_EXAMPLE_COM_USERNAME='tcontributor@example.com',
                BUGS_EXAMPLE_COM_PASSWORD='password',
            ),
        ), patch('webkitbugspy.Tracker._trackers', [bugzilla.Tracker(BUGZILLA)]):
            self.add_stack_with_bugs(repo)
            self.assertEqual(0, program.main(
                args=('pull-request', '--no-history', '--on', 'eng/parent'),
                path=self.path,
            ))
            self.assertEqual(0, program.main(args=('pull-request', '--no-history'), path=self.path))

            self.assertEqual(
                [issue.id for issue in bugzilla.Tracker(BUGZILLA).issue(2).related['depends_on']],
                [1],
            )

        self.assertEqual(
            captured.stdout.getvalue().count(f'{BUGZILLA}/show_bug.cgi?id=2 depends on'),
            1,
        )

    def test_pull_request_relates_radars(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), Environment(RADAR_USERNAME='tcontributor'), bmocks.Radar(
            issues=bmocks.ISSUES, projects=bmocks.PROJECTS,
        ), patch('webkitbugspy.Tracker._trackers', [radar.Tracker()]):
            self.add_stack_with_radars(repo)
            self.assertEqual(0, program.main(
                args=('pull-request', '--no-history', '--on', 'eng/parent'),
                path=self.path,
            ))

            child_issue = radar.Tracker().issue(2)
            self.assertEqual(
                [issue.id for issue in child_issue.related['blocked-by']],
                [1],
            )

        self.assertIn('rdar://2 blocked by rdar://1', captured.stdout.getvalue())

    def test_pull_request_relates_radars_once(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), Environment(RADAR_USERNAME='tcontributor'), bmocks.Radar(
            issues=bmocks.ISSUES, projects=bmocks.PROJECTS,
        ), patch('webkitbugspy.Tracker._trackers', [radar.Tracker()]):
            self.add_stack_with_radars(repo)
            self.assertEqual(0, program.main(args=('pull-request', '--no-history', '--on', 'eng/parent'), path=self.path))
            self.assertEqual(0, program.main(args=('pull-request', '--no-history'), path=self.path))

            self.assertEqual(
                [issue.id for issue in radar.Tracker().issue(2).related['blocked-by']],
                [1],
            )

        self.assertEqual(captured.stdout.getvalue().count('rdar://2 blocked by rdar://1'), 1)

    def test_issues_for_falls_back_to_branch_config(self):
        with OutputCapture(), mocks.local.Git(self.path) as repo, mocks.local.Svn(), bmocks.Bugzilla(
            BUGZILLA.split('://')[-1],
            issues=bmocks.ISSUES,
            environment=Environment(
                BUGS_EXAMPLE_COM_USERNAME='tcontributor@example.com',
                BUGS_EXAMPLE_COM_PASSWORD='password',
            ),
        ), patch('webkitbugspy.Tracker._trackers', [bugzilla.Tracker(BUGZILLA)]), MockTime:
            self.add_parent(repo)
            repo.edit_config('branch.eng/parent.bug', f'{BUGZILLA}/show_bug.cgi?id=1')

            issues = program.Stack.issues_for(local.Git(self.path), 'eng/parent')
            self.assertEqual(
                [issue.link for issue in issues],
                [f'{BUGZILLA}/show_bug.cgi?id=1'],
            )

    @classmethod
    def add_stack_with_bugs_and_radars(cls, repo):
        repo.commits['eng/parent'] = [
            repo.commits[repo.default_branch][-1],
            Commit(
                hash='06de5d56554e693db72313f4ca1fb969c30b8ccb',
                branch='eng/parent',
                author=CONTRIBUTOR,
                identifier='5.1@eng/parent',
                timestamp=1601668000,
                message=f'[Testing] Parent change\n{BUGZILLA}/show_bug.cgi?id=1\nrdar://1\n',
            ),
        ]
        repo.commits['eng/child'] = [
            repo.commits['eng/parent'][-1],
            Commit(
                hash='b8b921baaad2fd10bc9d0cc9e97f8fa1d6e5f4a1',
                branch='eng/child',
                author=CONTRIBUTOR,
                identifier='5.2@eng/child',
                timestamp=1601669000,
                message=f'[Testing] Child change\n{BUGZILLA}/show_bug.cgi?id=2\nrdar://2\n',
            ),
        ]
        repo.head = repo.commits['eng/child'][-1]
        return repo

    def test_pull_request_relates_each_tracker_to_its_own(self):
        with OutputCapture() as captured, mocks.remote.GitHub() as github, mocks.local.Git(
            self.path, remote=f'https://{github.remote}',
            remotes={'fork': f'https://{github.hosts[0]}/Contributor/WebKit'},
        ) as repo, mocks.local.Svn(), bmocks.Bugzilla(
            BUGZILLA.split('://')[-1],
            issues=bmocks.ISSUES,
            environment=Environment(
                BUGS_EXAMPLE_COM_USERNAME='tcontributor@example.com',
                BUGS_EXAMPLE_COM_PASSWORD='password',
                RADAR_USERNAME='tcontributor',
            ),
        ), bmocks.Radar(
            issues=bmocks.ISSUES, projects=bmocks.PROJECTS,
        ), patch('webkitbugspy.Tracker._trackers', [bugzilla.Tracker(BUGZILLA), radar.Tracker()]):
            self.add_stack_with_bugs_and_radars(repo)
            self.assertEqual(0, program.main(
                args=('pull-request', '--no-history', '--on', 'eng/parent'),
                path=self.path,
            ))

            # A bug depends on the parent's bug, a radar is blocked by the parent's radar,
            # rather than either being related to whichever issue came first
            self.assertEqual(
                [issue.id for issue in bugzilla.Tracker(BUGZILLA).issue(2).related['depends_on']],
                [1],
            )
            self.assertEqual(
                [issue.id for issue in bugzilla.Tracker(BUGZILLA).issue(1).related['blocks']],
                [2],
            )
            self.assertEqual(
                [issue.id for issue in radar.Tracker().issue(2).related['blocked-by']],
                [1],
            )

        self.assertIn('depends on', captured.stdout.getvalue())
        self.assertIn('rdar://2 blocked by rdar://1', captured.stdout.getvalue())
