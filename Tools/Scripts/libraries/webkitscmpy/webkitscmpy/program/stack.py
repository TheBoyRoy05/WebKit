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

import os
import re
import sys

from collections import namedtuple
from .command import Command

from webkitbugspy import Tracker, radar
from webkitcorepy import run, Version
from webkitscmpy import local, log

Rebase = namedtuple('Rebase', ('branch', 'onto', 'base', 'update_refs'))


class Stack(Command):
    name = 'stack'
    help = 'Manage a stack of dependent development branches'

    PARENT_KEY = 'stack-parent'
    BASE_KEY = 'stack-base'
    HEADER = 'Stacked pull requests, bottom of the stack first:'
    UPDATE_REFS_VERSION = Version(2, 38)
    PULL_REQUEST_RE = re.compile(r'(?:^|/pull/)(?P<number>\d+)(?:/|$)')

    @classmethod
    def parser(cls, parser, loggers=None):
        parser.add_argument(
            '--rebase', dest='rebase', action='store_true', default=False,
            help='Rebase every branch in the stack onto the branch beneath it',
        )
        parser.add_argument(
            '--on', '--stacked-on',
            dest='parent', type=str, default=None,
            help='Stack the current development branch on the provided branch, pull-request or issue, '
                 'replaying it onto that branch if it does not already sit on top of it',
        )
        parser.add_argument(
            '--unstack',
            dest='unstack', action='store_true', default=False,
            help='Forget which branch the current development branch is stacked on',
        )
        parser.add_argument(
            '--remote', dest='remote', type=str, default=None,
            help="Look up the stack's pull-requests on the provided remote, and rebase the bottom "
                 "of the stack on that remote's production branch",
        )

    @classmethod
    def _key_for(cls, branch, key=None):
        return f'branch.{branch}.{key or cls.PARENT_KEY}'

    @classmethod
    def _base(cls, git, branch):
        """The parent's tip as of the last time 'branch' was known to sit on top of it."""
        return git.config().get(cls._key_for(branch, cls.BASE_KEY)) or None

    @classmethod
    def _set_base(cls, git, branch, parent):
        if not (tip := git.commit(branch=parent, include_log=False, include_identifier=False)):
            sys.stderr.write(f"Failed to resolve the tip of '{parent}'\n")
            return 1
        command = [git.executable(), 'config', cls._key_for(branch, cls.BASE_KEY), tip.hash]
        if run(command, cwd=git.root_path, capture_output=True).returncode:
            sys.stderr.write(f"Failed to record where '{branch}' sits on '{parent}'\n")
            return 1
        git.config(cached=False)
        return 0

    @classmethod
    def parent(cls, git, branch):
        if not isinstance(git, local.Git) or not branch:
            return None

        candidate = git.config().get(cls._key_for(branch))
        if not candidate or candidate == branch:
            return None

        if candidate not in git.branches_for(remote=False):
            log.warning(f"'{branch}' is stacked on '{candidate}', which no longer exists in this checkout")
            return None
        return candidate

    @classmethod
    def _set_parent(cls, git, branch, parent):
        command = [git.executable(), 'config', cls._key_for(branch), parent]
        if run(command, cwd=git.root_path, capture_output=True).returncode:
            sys.stderr.write(f"Failed to record that '{branch}' is stacked on '{parent}'\n")
            return 1
        git.config(cached=False)
        return 0

    @classmethod
    def _unset_parent(cls, git, branch):
        for key in (cls.PARENT_KEY, cls.BASE_KEY):
            command = [git.executable(), 'config', '--unset', cls._key_for(branch, key)]
            if run(command, cwd=git.root_path, capture_output=True).returncode and key == cls.PARENT_KEY:
                sys.stderr.write(f"Failed to forget which branch '{branch}' is stacked on\n")
                return 1
        git.config(cached=False)
        return 0

    @classmethod
    def _children(cls, git, branch):
        prefix, suffix = 'branch.', f'.{cls.PARENT_KEY}'
        result = []
        for key, value in git.config().items():
            if value != branch or not key.startswith(prefix) or not key.endswith(suffix):
                continue
            candidate = key[len(prefix):-len(suffix)]
            if cls.parent(git, candidate) == branch:
                result.append(candidate)
        return sorted(result)

    @classmethod
    def _ancestors(cls, git, branch):
        result = []
        candidate = branch
        while candidate := cls.parent(git, candidate):
            if candidate == branch or candidate in result:
                sys.stderr.write(f"'{candidate}' is part of a cycle of stacked branches\n")
                return None
            result.append(candidate)
        return result[::-1]  # Bottom of stack to top

    @classmethod
    def _descendants(cls, git, branch):  # DFS so children come before siblings
        result = []
        stack = list(reversed(cls._children(git, branch)))
        while stack:
            candidate = stack.pop()
            if candidate == branch or candidate in result:
                sys.stderr.write(f"'{candidate}' is part of a cycle of stacked branches\n")
                return None
            result.append(candidate)
            stack.extend(reversed(cls._children(git, candidate)))
        return result

    @classmethod
    def members(cls, git, branch):
        if (below := cls._ancestors(git, branch)) is None:
            return None
        root = below[0] if below else branch
        if (above := cls._descendants(git, root)) is None:
            return None
        return [root] + above

    @classmethod
    def _pull_request_for(cls, git, branch, remote_repo):
        if not remote_repo or not remote_repo.pull_requests:
            return None
        if not git.config().get(f'branch.{branch}.target'):
            return None

        # Avoids a circular import
        from .pull_request import PullRequest
        return PullRequest.find_existing_pull_request(git, remote_repo, branch=branch)

    @classmethod
    def describe(cls, git, branch, remote_repo=None):
        if (members := cls.members(git, branch)) is None:
            return None
        if len(members) < 2:
            return []

        depth = {}
        lines = [cls.HEADER]
        for member in members:
            depth[member] = depth.get(cls.parent(git, member), -1) + 1
            pull_request = cls._pull_request_for(git, member, remote_repo)

            annotations = []
            if member == branch:
                annotations.append('this pull request')
            elif remote_repo and not pull_request:
                annotations.append('not uploaded')
            elif pull_request and pull_request.merged:
                annotations.append('merged')
            elif pull_request and pull_request.opened is False:
                annotations.append('closed')

            number = f'#{pull_request.number} ' if pull_request else ''
            described = f" ({', '.join(annotations)})" if annotations else ''
            lines.append(f"{'    ' * depth[member]}- {number}{member}{described}")
        return lines

    @classmethod
    def _supports_update_refs(cls, git):
        """git rebase --update-ref is available in 2.38."""
        result = run([git.executable(), '--version'], cwd=git.root_path, capture_output=True, encoding='utf-8')
        if result.returncode or not (match := re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', result.stdout)):
            return False
        return Version(*[int(group) for group in match.groups(default='0')]) >= cls.UPDATE_REFS_VERSION

    @classmethod
    def _is_contiguous(cls, git, branch, parent):
        """Whether 'branch' still descends from its parent's tip, so both can replay in one rebase."""
        tip = git.commit(branch=parent, include_log=False, include_identifier=False)
        return bool(tip) and cls._base(git, branch) == tip.hash

    @classmethod
    def _rebase_plan(cls, git, members, trunk, branch_point) -> list[Rebase]:
        """One rebase per run of branches that can replay together, bottom of the stack first."""
        def rebase_for(member, parent):
            if member == members[0]:
                return Rebase(member, trunk, branch_point.hash, False)
            return Rebase(member, parent, cls._base(git, member) or parent, False)

        result = []
        update_refs = cls._supports_update_refs(git)

        for member in members:
            parent = cls.parent(git, member)
            is_first_child = result and result[-1].branch == parent

            if update_refs and is_first_child and cls._is_contiguous(git, member, parent):
                result[-1] = result[-1]._replace(branch=member, update_refs=True)
            else:
                result.append(rebase_for(member, parent))
        return result

    @classmethod
    def rebase(cls, git, remote=None, prune=None):
        # CHECKS
        if not (branch := git.branch):
            sys.stderr.write('HEAD is not on a branch, so there is no stack to rebase\n')
            if any(os.path.isdir(os.path.join(git.git_directory, candidate)) for candidate in ('rebase-merge', 'rebase-apply')):
                sys.stderr.write("Finish the rebase in progress with 'git rebase --continue' or 'git rebase --abort'\n")
            return 1

        if (members := cls.members(git, branch)) is None:
            return 1

        remote = remote or git.default_remote
        root = members[0]
        if not (branch_point := git.branch_point(ref=root)):
            sys.stderr.write(f"Failed to determine where '{root}' diverged from a production branch\n")
            return 1

        if git.branch != branch_point.branch and not git.is_worktree and git.fetch(
            branch=branch_point.branch, remote=remote, prune=prune,
        ):
            sys.stderr.write(f"Failed to fetch '{branch_point.branch}' from '{remote}'\n")
            return 1

        # REBASE
        trunk = f'remotes/{remote}/{branch_point.branch}'
        plan = cls._rebase_plan(git, members, trunk, branch_point)
        for step in plan:
            if cls._rebase_onto(git, step):
                sys.stderr.write(
                    f"Then run 'git rebase --continue' followed by "
                    f"'{os.path.basename(sys.argv[0])} stack --rebase' to replay the rest of the stack\n"
                )
                return 1

        for member in members[1:]:
            if cls._set_base(git, member, cls.parent(git, member)):
                return 1

        # CLEANUP
        command = [git.executable(), 'checkout', branch]
        if plan[-1].branch != branch and run(command, cwd=git.root_path, capture_output=True).returncode:
            sys.stderr.write(f"Failed to return to '{branch}'\n")
            return 1
        return 0

    @classmethod
    def _rebase_onto(cls, git, step):
        log.info(f"Rebasing '{step.branch}' on '{step.onto}'...")
        command = [git.executable(), 'rebase', '--onto', step.onto, step.base, step.branch, '--autostash']
        if step.update_refs:
            command.append('--update-refs')

        if run(command, cwd=git.root_path).returncode:
            sys.stderr.write(f"Failed to rebase '{step.branch}' on '{step.onto},' please resolve conflicts\n")
            return 1
        return 0

    @classmethod
    def pull_request_number(cls, argument):
        match = cls.PULL_REQUEST_RE.search(str(argument))
        return int(match.group('number')) if match else None

    @classmethod
    def _branch_for_pull_request(cls, git, remote_repo, number, branch=None):
        """The local branch a pull-request is from, and 1 if it exists but cannot be stacked on."""
        if not remote_repo or not remote_repo.pull_requests:
            log.warning(f"Cannot look up pull-request #{number}, '{git.root_path}' has no remote which tracks them")
            return None, 0
        if not (pull_request := remote_repo.pull_requests.get(number)):
            return None, 0

        if pull_request.merged:
            sys.stderr.write(f"'{pull_request}' has already been merged, there is nothing to stack on\n")
            return None, 1
        if not pull_request.head:
            sys.stderr.write(f"Failed to determine which branch '{pull_request}' is from\n")
            return None, 1
        if pull_request.head not in git.branches_for(remote=False):
            sys.stderr.write(f"'{pull_request}' is from '{pull_request.head},' which does not exist in this checkout\n")
            sys.stderr.write(f"Fetch that branch before stacking '{branch or git.branch}' on it\n")
            return None, 1
        return pull_request.head, 0

    @classmethod
    def _branch_for_issue(cls, git, argument):
        """The local branch recorded against an issue, matched on the numbers in its url."""
        if not (numbers := set(re.findall(r'\d+', str(argument)))):
            return None

        # 'git config --get-regexp' reports every value, where config() keeps only the last
        command = [git.executable(), 'config', '--get-regexp', r'branch\..*\.bug']
        result = run(command, cwd=git.root_path, capture_output=True, encoding='utf-8')
        if result.returncode:
            return None

        prefix, suffix = 'branch.', '.bug'
        matches = set()
        for line in result.stdout.splitlines():
            key, _, value = line.partition(' ')
            if not key.startswith(prefix) or not key.endswith(suffix):
                continue
            if numbers & set(re.findall(r'\d+', value)):
                matches.add(key[len(prefix):-len(suffix)])

        if not matches:
            return None
        candidate = sorted(matches)[0]  # Sorted so the same argument always picks the same branch
        if len(matches) > 1:
            log.warning(f"'{argument}' matches {', '.join(sorted(matches))}, stacking on '{candidate}'")
        return candidate

    @classmethod
    def resolve(cls, git, argument, remote_repo=None, branch=None):
        """The branch in this checkout a branch name, pull-request, or issue refers to."""
        from .branch import Branch

        branches = git.branches_for(remote=False)
        candidate = argument if argument in branches else Branch.normalize_branch_name(argument, repository=git)

        # Each lookup is only tried if the ones before it did not already name a branch we have
        if candidate not in branches:
            if (number := cls.pull_request_number(argument)) is not None:
                head, result = cls._branch_for_pull_request(git, remote_repo, number, branch=branch)
                if result:
                    return None
                candidate = head or candidate
            if candidate not in branches:
                candidate = cls._branch_for_issue(git, argument) or candidate

        if candidate not in branches:
            sys.stderr.write(f"Could not find '{argument}' as a branch, pull-request, or issue in this checkout\n")
            return None
        if not git.dev_branches.match(candidate):
            sys.stderr.write(f"'{candidate}' is not a development branch, a branch cannot be stacked on it\n")
            return None
        if candidate == branch:
            sys.stderr.write(f"'{branch}' cannot be stacked on itself\n")
            return None
        return candidate

    @classmethod
    def issues_for(cls, git, branch, config=None):
        commit = git.commit(branch=branch, include_log=True, include_identifier=False)
        if commit and commit.issues:
            return commit.issues
        config = git.config() if config is None else config
        issue = Tracker.from_string(config.get(f'branch.{branch}.bug') or '')
        return [issue] if issue else []

    @classmethod
    def _matching_issues(cls, git, branch, parent, issues=None):
        """Each of a branch's issues paired with the parent issue tracked by the same tracker."""
        parent_issues = cls.issues_for(git, parent)
        if not parent_issues:
            log.info(f"'{parent}' has no associated issue, nothing to relate to '{branch}'")
            return []
        if issues is None:
            issues = cls.issues_for(git, branch)

        result = []
        for issue in issues:
            is_radar = isinstance(issue.tracker, radar.Tracker)
            match = next((
                candidate for candidate in parent_issues
                if isinstance(candidate.tracker, radar.Tracker) == is_radar
            ), None)
            if match and match.link != issue.link:
                result.append((issue, match, 'blocked_by' if is_radar else 'depends_on'))
        return result

    @classmethod
    def _apply_relations(cls, git, branch, parent, issues=None, related=True):
        """Bring each issue's dependency on the parent's issue to 'related', leaving the rest alone."""
        for issue, match, relation in cls._matching_issues(git, branch, parent, issues=issues):
            label = f"{'' if related else 'no longer '}{relation.replace('_', ' ')}"
            try:
                existing = (issue.related or {}).get(issue.tracker.relation_key(relation)) or []
                if any(candidate.link == match.link for candidate in existing) == related:
                    log.info(f'{issue.link} already {label} {match.link}')
                    continue

                change = issue.relate if related else issue.unrelate
                if change(**{relation: match}):
                    print(f'{issue.link} {label} {match.link}')
                else:
                    sys.stderr.write(f'Failed to record that {issue.link} {label} {match.link}\n')
            except (NotImplementedError, TypeError) as error:
                log.warning(f'Cannot record that {issue.link} {label} {match.link}: {error}')
        return 0

    @classmethod
    def relate_issues(cls, git, branch, parent, issues=None):
        return cls._apply_relations(git, branch, parent, issues=issues, related=True)

    @classmethod
    def unrelate_issues(cls, git, branch, parent, issues=None):
        return cls._apply_relations(git, branch, parent, issues=issues, related=False)

    @classmethod
    def stack_on(cls, git, branch, parent):
        """Record that 'branch' is stacked on 'parent,' and put it there."""
        return cls._set_parent(git, branch, parent) or cls._restack(git, branch, parent)

    @classmethod
    def _restack(cls, git, branch, parent):
        command = [git.executable(), 'merge-base', '--is-ancestor', parent, branch]
        if not run(command, cwd=git.root_path, capture_output=True).returncode:
            return cls._set_base(git, branch, parent)

        if not (base := cls._base(git, branch)):
            command = [git.executable(), 'merge-base', parent, branch]
            result = run(command, cwd=git.root_path, capture_output=True, encoding='utf-8')
            if result.returncode:
                sys.stderr.write(f"Failed to find where '{branch}' and '{parent}' diverged\n")
                return 1
            base = result.stdout.strip()

        if cls._rebase_onto(git, Rebase(branch, parent, base, False)):
            sys.stderr.write(f"Then run 'git rebase --continue' to finish stacking '{branch}' on '{parent}'\n")
            return 1
        return cls._set_base(git, branch, parent)

    @classmethod
    def main(cls, args, repository, **kwargs):
        if not isinstance(repository, local.Git):
            sys.stderr.write(f"Can only '{cls.name}' on a native Git repository\n")
            return 1

        git = repository
        branch = git.branch
        if args.parent and args.unstack:
            sys.stderr.write(f"Cannot both set and unset the branch '{branch}' is stacked on\n")
            return 1

        if args.parent:
            if not git.is_suitable_branch_for_pull_request(branch, args.remote or git.default_remote):
                sys.stderr.write(f"'{branch}' is not a development branch, it cannot be stacked on another branch\n")
                return 1
            remote_repo = git.remote(name=args.remote or git.default_remote)
            if not (parent := cls.resolve(git, args.parent, remote_repo=remote_repo, branch=branch)):
                return 1
            if (descendants := cls._descendants(git, branch)) is None:
                return 1
            if parent in descendants:
                sys.stderr.write(
                    f"'{parent}' is stacked on '{branch},' stacking '{branch}' on it would create a cycle\n"
                )
                return 1
            if cls.stack_on(git, branch, parent):
                return 1
            print(f"'{branch}' is stacked on '{parent}'")
            return cls.rebase(git, remote=args.remote) if args.rebase else 0

        if args.unstack:
            if parent := cls.parent(git, branch):
                cls.unrelate_issues(git, branch, parent)
            was_stacked = bool(parent)
            if cls._unset_parent(git, branch):
                return 1
            print(f"'{branch}' is no longer stacked on another branch")

            # Nothing sits beneath it any more, so replay it onto the branch it will be merged into
            return cls.rebase(git, remote=args.remote) if was_stacked else 0

        if args.rebase and (result := cls.rebase(git, remote=args.remote)):
            return result

        remote_repo = git.remote(name=args.remote or git.default_remote)
        if (lines := cls.describe(git, branch, remote_repo=remote_repo)) is None:
            return 1
        if not lines:
            print(f"'{branch}' is not part of a stack")
            return 0
        print('\n'.join(lines))
        return 0
