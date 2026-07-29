export const meta = {
  name: 'pr-review-deep',
  description: 'Thorough adversarial review: six lenses, three-way refutation, completeness critic',
  whenToUse:
    'High-risk diffs: data correctness, money, auth, migrations, secrets, concurrency, or a shared helper with many callers. Several times the cost of pr-review-quick, so reach for it deliberately.',
  phases: [{ title: 'Review' }, { title: 'Refute' }, { title: 'Critic' }],
}

// args:
//   repo  - path to the checkout under review (default: current directory)
//   base  - what to diff against (default: origin/main)
//   scope - one line on what the PR is meant to do. Load-bearing; see the skill.
//   focus - optional extra instruction appended to every reviewer, for project-specific
//           concerns ("this is on the payment path", "timestamps here are always UTC").
// args can arrive as a JSON-encoded STRING depending on how the caller serialises it. Every field
// then reads as undefined, and the old `repo || '.'` default silently pointed all reviewers at the
// session's working directory — producing confident, well-evidenced findings about a completely
// different branch. Nothing in that output says it reviewed the wrong tree. Parse it, and refuse
// to guess a repo.
let input = args
if (typeof input === 'string') {
  try {
    input = JSON.parse(input)
  } catch {
    throw new Error('args was a string and is not valid JSON: ' + input.slice(0, 200))
  }
}
if (!input || !input.repo) {
  throw new Error('args.repo is required: the absolute path of the checkout to review. There is no default — a wrong-tree review is indistinguishable from a good one.')
}
const repo = input.repo
const base = input.base || 'origin/main'
const scope = input.scope || 'not supplied — infer it from the diff'
const focus = input.focus || ''

const CONTEXT = [
  'You are reviewing an UNMERGED branch in the checkout at ' + repo + '.',
  '',
  'EVERY command must be scoped to that checkout with git -C, and you must not read, grep or open',
  'a single file outside it. Your shell starts in a DIFFERENT directory — the session root — which',
  'may be another branch carrying its own uncommitted work. A review that silently reads the wrong',
  'tree returns confident findings about code that is not under review, and that is indistinguishable',
  'from a good result until someone checks the file paths. Treat the path as a hard boundary.',
  '',
  'Pin the target first, and state what you found:',
  '  git -C ' + repo + ' rev-parse --short HEAD',
  '  git -C ' + repo + ' diff --stat ' + base + '...HEAD',
  'If that diff is empty, or names files unrelated to the scope below, STOP and report that rather',
  'than reviewing whatever you can find.',
  '',
  'Read the diff:   git -C ' + repo + ' diff ' + base + '...HEAD',
  'Changed files:   git -C ' + repo + ' diff --name-only ' + base + '...HEAD',
  '',
  'WHAT THE AUTHOR SAYS IT DOES: ' + scope,
  focus ? 'PROJECT-SPECIFIC CONCERNS: ' + focus : '',
  '',
  "Read-only commands only — git, grep, and the project's own test/lint/type commands where you can",
  'infer them (Makefile, package.json, pyproject.toml, justfile). Never modify, commit or push.',
  '',
  'Report at most 4 findings, most serious first. Prefer reporting NOTHING to reporting something',
  'weak — a review that cries wolf gets ignored, which is worse than no review at all.',
  'A finding must be a defect in THIS diff with a concrete failure: given these inputs or this state,',
  'the code does the wrong thing. Style preferences, hypotheticals, and pre-existing behaviour the',
  'diff merely touches are not findings.',
  'Where you can, REPRODUCE the failure with a read-only command and paste what you ran. A reproduced',
  'finding survives refutation; an argued one usually does not.',
]
  .filter(Boolean)
  .join('\n')

const FINDINGS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      maxItems: 4,
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['title', 'file', 'line', 'severity', 'why_it_breaks'],
        properties: {
          title: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'number' },
          severity: { type: 'string', enum: ['high', 'medium', 'low'] },
          why_it_breaks: { type: 'string', description: 'concrete inputs/state -> wrong behaviour' },
          evidence: { type: 'string', description: 'command run and what it showed' },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['refuted', 'reasoning'],
  properties: {
    refuted: { type: 'boolean' },
    reasoning: { type: 'string' },
    evidence: { type: 'string' },
  },
}

const DIMENSIONS = [
  {
    key: 'correctness',
    lens:
      'DIMENSION: correctness of the changed lines. Off-by-one, inverted conditions, wrong variable,\n' +
      'unhandled None, a rebase that merged two hunks into something neither author wrote. Walk the\n' +
      'real branches with real values rather than reading for plausibility.',
  },
  {
    key: 'silent-wrongness',
    lens:
      'DIMENSION: ways this produces a CONFIDENT WRONG ANSWER rather than an error — the failure mode\n' +
      'that hurts most in this codebase. Data silently dropped instead of raising; a default wrong for\n' +
      'some callers; a swallowed exception; a heuristic that can classify everything one way; a value\n' +
      'that looks plausible but answers a different question than the one asked. Ask of each change: if\n' +
      'this were wrong, what would tell us? If nothing would, say so.',
  },
  {
    key: 'blast-radius',
    lens:
      'DIMENSION: what changes for callers the author was not thinking about. grep every caller of every\n' +
      'changed function. Does a shared helper now behave differently for an existing caller? Does a\n' +
      'config default change existing behaviour? Do two code paths that must agree now disagree?',
  },
  {
    key: 'secrets-and-io',
    lens:
      'DIMENSION: secrets, logging and anything leaving the process. Can a credential reach a log line,\n' +
      'an exception message, Sentry, a cached file, or a committed config? Does an error message\n' +
      'interpolate a resolved URL rather than its placeholder template? Are new files written where\n' +
      'they could be committed or uploaded as a CI artifact? Are new network calls bounded and retried\n' +
      'sanely, and can one slow dependency hang the whole job?',
  },
  {
    key: 'tests',
    lens:
      'DIMENSION: are the tests honest? For each new or changed test ask: can it pass VACUOUSLY (empty\n' +
      'collection, no files found, assertion on nothing)? Does it merely restate the implementation?\n' +
      'Would it actually FAIL if the behaviour it names regressed — check by reasoning about a specific\n' +
      'mutation. Does the diff change behaviour that NO test covers? Does a test depend on shared\n' +
      'database state or on running in a particular order?',
  },
  {
    key: 'operability',
    lens:
      'DIMENSION: what happens in production. If this misbehaves at 3am, is it visible? Does it add an\n' +
      'unbounded loop, an uncapped fetch, or a per-item query in a hot path? Does it add a new error\n' +
      'signal that could flood the alerting channel with false positives? Is the failure mode a loud\n' +
      'stop or a quiet drift? Does anything here need a backfill, migration or config change to be\n' +
      'deployed with it, and does the PR say so?',
  },
]

phase('Review')
const reviewed = await pipeline(
  DIMENSIONS,
  (d) => agent(CONTEXT + '\n' + d.lens, { label: 'review:' + d.key, phase: 'Review', schema: FINDINGS_SCHEMA }),
  (review, dimension) => {
    const found = (review && review.findings) || []
    if (!found.length) return []
    // Three refuters per finding, each with a different angle. Redundant skeptics agree with each
    // other; differing ones catch different kinds of wrongness. Majority rules, so one contrarian
    // cannot sink a real finding and one credulous reader cannot save a bad one.
    const angles = [
      'Verify the mechanism: does the code actually do what the claim says, line by line?',
      'Check scope: is this introduced by THIS diff, or pre-existing behaviour it merely touches? Is it already handled or guarded elsewhere?',
      'Try to REPRODUCE it with a read-only command. If you cannot make it happen, say so.',
    ]
    return parallel(
      found.map((f) => () =>
        parallel(
          angles.map((angle) => () =>
            agent(
              CONTEXT +
                '\n' +
                [
                  'A reviewer claims this defect. Your job is to REFUTE it.',
                  '',
                  'CLAIM: ' + f.title,
                  'FILE: ' + f.file + ':' + f.line,
                  'WHY THEY SAY IT BREAKS: ' + f.why_it_breaks,
                  'THEIR EVIDENCE: ' + (f.evidence || 'none given'),
                  '',
                  'YOUR ANGLE: ' + angle,
                  '',
                  'Set refuted=true if the claim is wrong, already handled, pre-existing, or cannot occur.',
                  'Set refuted=false ONLY if the defect is real. Default to refuted=true when uncertain —',
                  'but never refute something you actually reproduced.',
                ].join('\n'),
              { label: 'refute:' + dimension.key, phase: 'Refute', schema: VERDICT_SCHEMA }
            )
          )
        ).then((votes) => {
          const cast = votes.filter(Boolean)
          const survived = cast.filter((v) => v.refuted === false).length
          return { ...f, dimension: dimension.key, votes: cast, survived: survived >= 2 }
        })
      )
    )
  }
)

const all = reviewed.flat().filter(Boolean)
const confirmed = all.filter((f) => f.survived)
log(all.length + ' raised, ' + confirmed.length + ' survived a three-way refutation')

// What nobody looked at is usually where the bug is. The lenses above are fixed, so this asks
// what the fixed set structurally cannot see.
phase('Critic')
const critic = await agent(
  CONTEXT +
    '\n' +
    [
      'Six reviewers have already examined this diff along these lenses: ' +
        DIMENSIONS.map((d) => d.key).join(', ') + '.',
      'They collectively confirmed these findings: ' +
        (confirmed.length ? confirmed.map((f) => f.title).join('; ') : 'none'),
      '',
      'Your job is NOT to repeat them. Identify what this set of lenses structurally could not see.',
      'Which changed file did nobody open? Which behaviour is asserted in the PR description but',
      'verified nowhere? What would a reviewer who knew this system deeply ask that none of them did?',
      'Answer in prose, briefly. If coverage was genuinely adequate, say so plainly rather than',
      'inventing a gap.',
    ].join('\n'),
  { label: 'critic:coverage', phase: 'Critic' }
)

return {
  confirmed,
  refuted: all.filter((f) => !f.survived).map((f) => f.title),
  coverage_gaps: critic,
}
