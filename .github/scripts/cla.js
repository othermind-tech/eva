'use strict'

// CLA check for pull requests. Used by .github/workflows/cla.yml through actions/github-script.
//
// What it does
//   1. Finds every contributor on the pull request: the PR author and the GitHub account of every commit author.
//   2. Maintainers (ALLOWLIST) and bots are exempt. Everyone else must have signed CLA.md.
//   3. A contributor signs by replying to the pull request with SIGN_PHRASE. The signature is recorded in
//      SIGNATURES_FILE on the SIGNATURES_BRANCH branch (a separate branch, so main can stay protected).
//   4. It keeps one comment on the pull request up to date and sets a commit status named STATUS_CONTEXT
//      (success or failure) that a repository ruleset can require.
//
// Security: this script only calls the GitHub API. It never checks out, builds or runs anything from the pull
// request, and comment text is only compared to a fixed phrase. The workflow that runs it must always check out
// the default branch, never the pull request head.

const SIGN_PHRASE = 'I have read the CLA Document and I hereby sign the CLA'
const RECHECK_PHRASE = 'recheck'
const MARKER = '<!-- cla-bot -->'
const SIGNATURES_BRANCH = 'cla-signatures'
const SIGNATURES_FILE = 'signatures/cla.json'
const STATUS_CONTEXT = 'CLA'
const CLA_FILE = 'CLA.md'
// GitHub logins that never need to sign (the maintainers). Compared case-insensitively.
const ALLOWLIST = ['anandcoding-cloud']

const lower = (s) => String(s || '').toLowerCase()
const squash = (s) => String(s || '').replace(/\s+/g, ' ').trim()

function isSignPhrase(body) {
  return lower(squash(body)) === lower(SIGN_PHRASE)
}

function isRecheck(body) {
  return lower(squash(body)) === RECHECK_PHRASE
}

function isExempt(user) {
  if (!user) return false
  return user.type === 'Bot' || lower(user.login).endsWith('[bot]') || ALLOWLIST.map(lower).includes(lower(user.login))
}

async function loadSignatures({ github, owner, repo }) {
  try {
    const res = await github.rest.repos.getContent({ owner, repo, path: SIGNATURES_FILE, ref: SIGNATURES_BRANCH })
    const text = Buffer.from(res.data.content, 'base64').toString('utf8')
    const parsed = JSON.parse(text)
    return { signatures: Array.isArray(parsed.signatures) ? parsed.signatures : [], sha: res.data.sha }
  } catch (err) {
    if (err && err.status === 404) return { signatures: [], sha: null }
    throw err
  }
}

async function ensureBranch({ github, owner, repo }) {
  try {
    await github.rest.git.getRef({ owner, repo, ref: `heads/${SIGNATURES_BRANCH}` })
    return
  } catch (err) {
    if (!err || err.status !== 404) throw err
  }
  const info = await github.rest.repos.get({ owner, repo })
  const base = await github.rest.git.getRef({ owner, repo, ref: `heads/${info.data.default_branch}` })
  await github.rest.git.createRef({ owner, repo, ref: `refs/heads/${SIGNATURES_BRANCH}`, sha: base.data.object.sha })
}

async function recordSignature({ github, owner, repo, signer, comment }) {
  await ensureBranch({ github, owner, repo })
  // Re-read just before writing so two signatures arriving together do not overwrite each other.
  for (let attempt = 0; attempt < 3; attempt++) {
    const { signatures, sha } = await loadSignatures({ github, owner, repo })
    if (signatures.some((s) => s.id === signer.id)) return false
    signatures.push({
      login: signer.login,
      id: signer.id,
      signedAt: new Date().toISOString(),
      comment: comment.html_url || null,
    })
    const content = Buffer.from(JSON.stringify({ signatures }, null, 2) + '\n', 'utf8').toString('base64')
    try {
      await github.rest.repos.createOrUpdateFileContents({
        owner,
        repo,
        path: SIGNATURES_FILE,
        branch: SIGNATURES_BRANCH,
        message: `CLA signed by ${signer.login}`,
        content,
        ...(sha ? { sha } : {}),
      })
      return true
    } catch (err) {
      if (!err || (err.status !== 409 && err.status !== 422)) throw err
    }
  }
  throw new Error('Could not record the CLA signature after several attempts')
}

function collectContributors(pr, commits) {
  const byId = new Map()
  const unlinked = []
  if (pr.user && !isExempt(pr.user)) byId.set(pr.user.id, { login: pr.user.login, id: pr.user.id })
  for (const c of commits) {
    if (c.author) {
      if (!isExempt(c.author)) byId.set(c.author.id, { login: c.author.login, id: c.author.id })
    } else {
      const name = (c.commit && c.commit.author && c.commit.author.name) || 'unknown author'
      if (!unlinked.includes(name)) unlinked.push(name)
    }
  }
  return { contributors: [...byId.values()], unlinked }
}

function buildComment({ unsigned, unlinked, claUrl }) {
  const lines = [MARKER]
  if (!unsigned.length && !unlinked.length) {
    lines.push('✅ **All contributors on this pull request have signed the CLA.** Thank you!')
    return lines.join('\n')
  }
  lines.push('**Thank you for your contribution!** Before this pull request can be merged, every contributor must sign the ' + `[Contributor License Agreement](${claUrl}).`)
  if (unsigned.length) {
    lines.push('', 'Still to sign: ' + unsigned.map((u) => `@${u.login}`).join(', '))
    lines.push(
      '',
      'To sign, reply to this pull request with exactly this sentence:',
      '',
      `> ${SIGN_PHRASE}`,
      '',
      'You only need to sign once. Reply `recheck` to run this check again.'
    )
  }
  if (unlinked.length) {
    lines.push(
      '',
      'Some commits are not linked to a GitHub account (author: ' + unlinked.join(', ') + '). ' +
        'Add the commit email address to your GitHub account, or amend the commits with an email GitHub knows, ' +
        'then push again.'
    )
  }
  return lines.join('\n')
}

async function upsertComment({ github, owner, repo, number, body, createIfMissing }) {
  const comments = await github.paginate(github.rest.issues.listComments, { owner, repo, issue_number: number, per_page: 100 })
  const mine = comments.find((c) => typeof c.body === 'string' && c.body.startsWith(MARKER))
  if (mine) {
    if (mine.body !== body) await github.rest.issues.updateComment({ owner, repo, comment_id: mine.id, body })
  } else if (createIfMissing) {
    await github.rest.issues.createComment({ owner, repo, issue_number: number, body })
  }
}

async function run({ github, context, core }) {
  const { owner, repo } = context.repo
  const payload = context.payload
  const isComment = context.eventName === 'issue_comment'

  if (isComment && !(payload.issue && payload.issue.pull_request)) return { skipped: 'comment is not on a pull request' }

  const number = isComment ? payload.issue.number : payload.pull_request.number
  const pr = isComment ? (await github.rest.pulls.get({ owner, repo, pull_number: number })).data : payload.pull_request
  if (pr.state && pr.state !== 'open' && isComment) return { skipped: 'pull request is not open' }

  const commits = await github.paginate(github.rest.pulls.listCommits, { owner, repo, pull_number: number, per_page: 100 })
  const { contributors, unlinked } = collectContributors(pr, commits)

  let { signatures } = await loadSignatures({ github, owner, repo })
  let signedIds = new Set(signatures.map((s) => s.id))
  let newlySigned = null

  if (isComment && isSignPhrase(payload.comment.body)) {
    const signer = contributors.find((c) => c.id === payload.comment.user.id)
    if (signer && !signedIds.has(signer.id)) {
      await recordSignature({ github, owner, repo, signer, comment: payload.comment })
      signedIds.add(signer.id)
      newlySigned = signer.login
    }
  }

  const unsigned = contributors.filter((c) => !signedIds.has(c.id))
  const claUrl = `https://github.com/${owner}/${repo}/blob/${(payload.repository && payload.repository.default_branch) || 'main'}/${CLA_FILE}`
  const ok = unsigned.length === 0 && unlinked.length === 0

  await upsertComment({
    github,
    owner,
    repo,
    number,
    body: buildComment({ unsigned, unlinked, claUrl }),
    createIfMissing: !ok,
  })

  await github.rest.repos.createCommitStatus({
    owner,
    repo,
    sha: pr.head.sha,
    state: ok ? 'success' : 'failure',
    context: STATUS_CONTEXT,
    description: ok ? 'All contributors have signed the CLA' : 'The CLA has not been signed by every contributor',
    target_url: claUrl,
  })

  if (core && core.info) core.info(`CLA check: ${ok ? 'passed' : 'failed'} (${unsigned.length} unsigned, ${unlinked.length} unlinked)`)
  return { ok, unsigned: unsigned.map((u) => u.login), unlinked, newlySigned }
}

module.exports = { run, isSignPhrase, isRecheck, isExempt, collectContributors, buildComment, SIGN_PHRASE, MARKER, SIGNATURES_BRANCH, SIGNATURES_FILE, STATUS_CONTEXT }
