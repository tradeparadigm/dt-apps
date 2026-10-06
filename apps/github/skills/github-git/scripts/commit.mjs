// Pushes the current branch's local commits to GitHub as one commit made
// through the GraphQL API, which GitHub signs, so it shows as Verified.
//
//   node commit.mjs [--dry-run] [--repo OWNER/NAME] [--base BRANCH] [--remote NAME]

import { execFileSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
const flag = name => args.includes(name);
const opt = name => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const fail = msg => { console.error(msg); process.exit(2); };

const git = (...a) => execFileSync('git', a, { encoding: 'utf8', maxBuffer: 1 << 30 }).trim();
const gitOk = (...a) => { try { git(...a); return true; } catch { return false; } };
const gitOr = (msg, ...a) => { try { return git(...a); } catch (e) { fail(`${msg}\n${(e.stderr || '').trim()}`); } };

const dryRun = flag('--dry-run');
const remote = opt('--remote') || 'origin';
const branch = git('rev-parse', '--abbrev-ref', 'HEAD');
if (branch === 'HEAD') fail('HEAD is detached; check out the branch to push');

function repoName() {
  if (opt('--repo')) return opt('--repo');
  const url = gitOr(`no remote named ${remote}; pass --remote NAME`, 'remote', 'get-url', remote);
  const m = url.match(/^(?:https:\/\/github\.com\/|git@github\.com:|ssh:\/\/git@github\.com\/)([^/]+\/[^/]+?)(?:\.git)?\/?$/);
  if (!m) fail(`${remote} is ${url}, which is not a github.com repository; pass --repo OWNER/NAME`);
  return m[1];
}
const repo = repoName();

function placeholder() {
  const here = dirname(fileURLToPath(import.meta.url));
  return execFileSync('sh', ['-c', '. "$1"; pick_cred REST GITHUB_REST_CRED; printf %s "$placeholder"', 'sh', join(here, 'creds.sh')],
    { encoding: 'utf8', stdio: ['ignore', 'pipe', 'inherit'] });
}

async function github(path, body) {
  const res = await fetch(`https://api.github.com${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${placeholder()}`, 'Content-Type': 'application/json', Accept: 'application/vnd.github+json' },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  if (!res.ok) fail(`POST ${path}: HTTP ${res.status}\n${text}`);
  return JSON.parse(text);
}

const remoteRef = name => `refs/remotes/${remote}/${name}`;
const fetchBranch = name => gitOr(`could not fetch ${name} from ${remote}`, 'fetch', '--quiet', remote, `+refs/heads/${name}:${remoteRef(name)}`);
const lsRemote = name => gitOr(`could not reach ${remote}`, 'ls-remote', remote, `refs/heads/${name}`).split('\t')[0];

let remoteHead = lsRemote(branch);
let createFrom;
if (remoteHead) {
  fetchBranch(branch);
} else {
  const base = opt('--base') || (git('ls-remote', '--symref', remote, 'HEAD').match(/^ref: refs\/heads\/(\S+)/m) || [])[1];
  if (!base) fail(`${branch} is not on ${remote} and its default branch is unknown; pass --base BRANCH`);
  fetchBranch(base);
  remoteHead = createFrom = gitOr(`no common history with ${remote}/${base}. In a shallow clone run git fetch --unshallow; on a branch made from another branch pass --base BRANCH.`, 'merge-base', 'HEAD', remoteRef(base));
}

if (!gitOk('merge-base', '--is-ancestor', remoteHead, 'HEAD')) {
  fail(`${remote}/${branch} has commits this branch does not. Pull or rebase onto it first.`);
}
if (git('rev-parse', 'HEAD') === remoteHead) fail(`nothing to push: ${branch} matches ${remote}`);

const additions = [];
const deletions = [];
const raw = git('diff', '--raw', '--no-renames', '-z', remoteHead, 'HEAD').split('\0').filter(Boolean);
for (let i = 0; i < raw.length; i += 2) {
  const [oldMode, newMode, , , status] = raw[i].slice(1).split(' ');
  const path = raw[i + 1];
  if (status === 'D') { deletions.push({ path }); continue; }
  const special = ['120000', '160000'].includes(newMode);
  const modeChange = status !== 'A' && oldMode !== newMode;
  const newExec = status === 'A' && newMode !== '100644';
  const why = special ? 'is a symlink or a submodule' : modeChange ? `changes mode ${oldMode} -> ${newMode}` : newExec ? 'is a new executable file' : '';
  if (why) fail(`${path} ${why}, which the API cannot commit. Use plain git push; that commit will not be Verified.`);
  const contents = execFileSync('git', ['show', `HEAD:${path}`], { maxBuffer: 1 << 30 }).toString('base64');
  additions.push({ path, contents });
}

const [headline, ...rest] = git('log', '-1', '--format=%B', 'HEAD').split('\n');
const subjects = git('log', '--reverse', '--format=%s', `${remoteHead}..HEAD`).split('\n');
let body = rest.join('\n').trim();
if (subjects.length > 1) body = [body, 'Includes:\n' + subjects.map(s => `- ${s}`).join('\n')].filter(Boolean).join('\n\n');

const input = {
  branch: { repositoryNameWithOwner: repo, branchName: branch },
  expectedHeadOid: remoteHead,
  message: body ? { headline, body } : { headline },
  fileChanges: { additions, deletions },
};

if (dryRun) {
  console.log(JSON.stringify({ createBranchAt: createFrom || null, input }, null, 2));
  process.exit(0);
}

if (createFrom) await github(`/repos/${repo}/git/refs`, { ref: `refs/heads/${branch}`, sha: createFrom });

const query = 'mutation($i:CreateCommitOnBranchInput!){createCommitOnBranch(input:$i){commit{oid url signature{isValid}}}}';
const out = await github('/graphql', { query, variables: { i: input } });
if (out.errors) fail(`GitHub refused the commit:\n${JSON.stringify(out.errors, null, 2)}`);
const commit = out.data.createCommitOnBranch.commit;

fetchBranch(branch);
if (git('rev-parse', `${commit.oid}^{tree}`) !== git('rev-parse', 'HEAD^{tree}')) {
  fail(`pushed ${commit.oid}, but its tree differs from HEAD. Run git fetch and compare before going on.`);
}
git('reset', '--soft', commit.oid);
gitOk('branch', '--set-upstream-to', `${remote}/${branch}`);
console.log(`${commit.url}\nsigned by GitHub: ${commit.signature?.isValid ? 'yes' : 'no'}`);
