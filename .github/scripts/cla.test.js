'use strict'

// Run with: node --test .github/scripts/
const test = require('node:test')
const assert = require('node:assert/strict')
const cla = require('./cla.js')

const OWNER = 'othermind-tech'
const REPO = 'eva'

function makeGithub({ commits = [], signatures = null, comments = [], pr }) {
  const state = {
    signatures: signatures ? { signatures: [...signatures] } : null,
    sha: signatures ? 'sha-1' : null,
    branchExists: signatures !== null,
    comments: comments.map((c, i) => ({ id: i + 1, ...c })),
    statuses: [],
    writes: 0,
  }
  const notFound = () => Object.assign(new Error('Not Found'), { status: 404 })
  const github = {
    paginate: async (fn) => (fn === github.rest.pulls.listCommits ? commits : state.comments),
    rest: {
      pulls: {
        get: async () => ({ data: pr }),
        listCommits: async () => ({ data: commits }),
      },
      issues: {
        listComments: async () => ({ data: state.comments }),
        createComment: async ({ body }) => state.comments.push({ id: state.comments.length + 1, body }),
        updateComment: async ({ comment_id, body }) => {
          state.comments.find((c) => c.id === comment_id).body = body
        },
      },
      repos: {
        get: async () => ({ data: { default_branch: 'main' } }),
        getContent: async () => {
          if (!state.signatures) throw notFound()
          return { data: { content: Buffer.from(JSON.stringify(state.signatures)).toString('base64'), sha: state.sha } }
        },
        createOrUpdateFileContents: async ({ content }) => {
          state.signatures = JSON.parse(Buffer.from(content, 'base64').toString('utf8'))
          state.sha = 'sha-' + ++state.writes
          state.branchExists = true
        },
        createCommitStatus: async (s) => state.statuses.push(s),
      },
      git: {
        getRef: async ({ ref }) => {
          if (ref.startsWith('heads/cla-signatures') && !state.branchExists) throw notFound()
          return { data: { object: { sha: 'abc' } } }
        },
        createRef: async () => {
          state.branchExists = true
        },
      },
    },
  }
  return { github, state }
}

const user = (login, id, extra = {}) => ({ login, id, type: 'User', ...extra })
const prOf = (author) => ({ number: 7, state: 'open', user: author, head: { sha: 'headsha' } })
const ctx = (eventName, payload) => ({ eventName, repo: { owner: OWNER, repo: REPO }, payload: { repository: { default_branch: 'main' }, ...payload } })
const commitBy = (u) => ({ author: u, commit: { author: { name: u ? u.login : 'Ghost' } } })

test('sign phrase matching ignores case and whitespace but nothing else', () => {
  assert.ok(cla.isSignPhrase('I have read the CLA Document and I hereby sign the CLA'))
  assert.ok(cla.isSignPhrase('  i have read the   cla document and i hereby sign the cla \n'))
  assert.ok(!cla.isSignPhrase('I have read the CLA Document and I hereby sign the CLA, thanks'))
  assert.ok(!cla.isSignPhrase('signed'))
})

test('maintainers and bots are exempt', () => {
  assert.ok(cla.isExempt(user('AnandCoding-Cloud', 1)))
  assert.ok(cla.isExempt({ login: 'dependabot[bot]', id: 2, type: 'Bot' }))
  assert.ok(!cla.isExempt(user('someone', 3)))
})

test('new external contributor fails and gets one instructions comment', async () => {
  const alice = user('alice', 10)
  const { github, state } = makeGithub({ pr: prOf(alice), commits: [commitBy(alice)] })
  const res = await cla.run({ github, context: ctx('pull_request_target', { pull_request: prOf(alice) }) })
  assert.equal(res.ok, false)
  assert.deepEqual(res.unsigned, ['alice'])
  assert.equal(state.statuses.at(-1).state, 'failure')
  assert.equal(state.statuses.at(-1).context, 'CLA')
  assert.equal(state.comments.length, 1)
  assert.ok(state.comments[0].body.startsWith(cla.MARKER))
  assert.ok(state.comments[0].body.includes(cla.SIGN_PHRASE))
})

test('maintainer-only pull request passes without a comment', async () => {
  const me = user('anandcoding-cloud', 1)
  const { github, state } = makeGithub({ pr: prOf(me), commits: [commitBy(me)] })
  const res = await cla.run({ github, context: ctx('pull_request_target', { pull_request: prOf(me) }) })
  assert.equal(res.ok, true)
  assert.equal(state.statuses.at(-1).state, 'success')
  assert.equal(state.comments.length, 0)
})

test('signing records the signature once and turns the status green', async () => {
  const alice = user('alice', 10)
  const { github, state } = makeGithub({
    pr: prOf(alice),
    commits: [commitBy(alice)],
    comments: [{ body: cla.MARKER + '\nold text' }],
  })
  const payload = { issue: { number: 7, pull_request: {} }, comment: { body: cla.SIGN_PHRASE, user: alice, html_url: 'https://x/c/1' } }
  const res = await cla.run({ github, context: ctx('issue_comment', payload) })
  assert.equal(res.ok, true)
  assert.equal(res.newlySigned, 'alice')
  assert.equal(state.signatures.signatures.length, 1)
  assert.equal(state.signatures.signatures[0].id, 10)
  assert.equal(state.statuses.at(-1).state, 'success')
  assert.equal(state.comments.length, 1, 'existing bot comment is updated, not duplicated')
  assert.ok(state.comments[0].body.includes('All contributors'))

  const again = await cla.run({ github, context: ctx('issue_comment', payload) })
  assert.equal(again.newlySigned, null)
  assert.equal(state.signatures.signatures.length, 1)
})

test('someone else cannot sign on behalf of the author', async () => {
  const alice = user('alice', 10)
  const mallory = user('mallory', 99)
  const { github, state } = makeGithub({ pr: prOf(alice), commits: [commitBy(alice)] })
  const payload = { issue: { number: 7, pull_request: {} }, comment: { body: cla.SIGN_PHRASE, user: mallory } }
  const res = await cla.run({ github, context: ctx('issue_comment', payload) })
  assert.equal(res.ok, false)
  assert.equal(state.signatures, null)
})

test('every committer must sign, not only the pull request author', async () => {
  const alice = user('alice', 10)
  const bob = user('bob', 11)
  const { github } = makeGithub({
    pr: prOf(alice),
    commits: [commitBy(alice), commitBy(bob)],
    signatures: [{ login: 'alice', id: 10 }],
  })
  const res = await cla.run({ github, context: ctx('pull_request_target', { pull_request: prOf(alice) }) })
  assert.equal(res.ok, false)
  assert.deepEqual(res.unsigned, ['bob'])
})

test('commits not linked to a GitHub account fail the check', async () => {
  const alice = user('alice', 10)
  const { github } = makeGithub({ pr: prOf(alice), commits: [commitBy(alice), commitBy(null)], signatures: [{ login: 'alice', id: 10 }] })
  const res = await cla.run({ github, context: ctx('pull_request_target', { pull_request: prOf(alice) }) })
  assert.equal(res.ok, false)
  assert.deepEqual(res.unlinked, ['Ghost'])
})

test('comments on plain issues are ignored', async () => {
  const { github, state } = makeGithub({ pr: prOf(user('a', 1)) })
  const res = await cla.run({ github, context: ctx('issue_comment', { issue: { number: 3 }, comment: { body: 'hi', user: user('a', 1) } }) })
  assert.ok(res.skipped)
  assert.equal(state.statuses.length, 0)
})
