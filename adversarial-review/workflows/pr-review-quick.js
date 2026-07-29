export const meta = {
  name: 'pr-review-quick',
  description: 'Fast adversarial review of a branch diff: three reviewers, one refuter per finding',
  whenToUse:
    'Default review for any PR. Cheap enough to run every time. For a diff touching data correctness, money, auth, migrations or a widely-called helper, use pr-review-deep instead.',
  phases: [{ title: 'Review' }, { title: 'Refute' }],
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
  'Report at most 3 findings, the most serious only. Prefer reporting NOTHING to reporting something',
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
      maxItems: 3,
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

// Correctness and silent-wrongness are deliberately separate lenses. A reviewer asked simply for
// "bugs" reliably finds crashes and reliably misses the change that returns a plausible wrong
// answer, because nothing about that looks like an error.
const DIMENSIONS = [
  {
    key: 'correctness',
    lens: [
      'DIMENSION: correctness of the changed lines.',
      'Off-by-one, inverted condition, wrong variable, unhandled null, a rebase that fused two hunks',
      'into something neither author wrote. Walk the real branches with real values rather than',
      'reading for plausibility.',
    ].join('\n'),
  },
  {
    key: 'silent-wrongness',
    lens: [
      'DIMENSION: ways this produces a CONFIDENT WRONG ANSWER instead of an error.',
      'Data silently dropped rather than raising; a default that is wrong for some callers; a',
      'swallowed exception; a type narrowing or cast asserting a shape the data does not always have;',
      'a fallback that hides the very failure it was meant to surface; a value that looks right but',
      'answers a different question than the one asked.',
      'Ask of each change: if this were wrong, what would tell us? If nothing would, say so.',
    ].join('\n'),
  },
  {
    key: 'blast-radius',
    lens: [
      'DIMENSION: what changes for callers the author was not thinking about.',
      'grep every caller of every changed function or symbol. Does a shared helper now behave',
      'differently for an existing caller? Does a changed default alter existing behaviour? Do two',
      'code paths that must agree now disagree? Can a credential reach a log line, an error message,',
      'an error tracker, or a file written to disk?',
    ].join('\n'),
  },
]

phase('Review')
const results = await pipeline(
  DIMENSIONS,
  (d) => agent(CONTEXT + '\n\n' + d.lens, { label: 'review:' + d.key, phase: 'Review', schema: FINDINGS_SCHEMA }),
  (review, dimension) => {
    const found = (review && review.findings) || []
    if (!found.length) return []
    return parallel(
      found.map((f) => () =>
        agent(
          CONTEXT +
            '\n\n' +
            [
              'A reviewer claims this defect. Your job is to REFUTE it.',
              '',
              'CLAIM: ' + f.title,
              'FILE: ' + f.file + ':' + f.line,
              'WHY THEY SAY IT BREAKS: ' + f.why_it_breaks,
              'THEIR EVIDENCE: ' + (f.evidence || 'none given'),
              '',
              'Verify against the actual code and, where you can, run a read-only command showing the',
              'real behaviour. Set refuted=true if the claim is wrong, already handled elsewhere,',
              'describes pre-existing behaviour this diff does not change, or cannot occur.',
              'Set refuted=false ONLY if you can demonstrate the failure. Default to refuted=true when',
              'uncertain — but never refute something you actually reproduced.',
            ].join('\n'),
          { label: 'refute:' + dimension.key, phase: 'Refute', schema: VERDICT_SCHEMA }
        ).then((v) => ({ ...f, dimension: dimension.key, verdict: v }))
      )
    )
  }
)

const all = results.flat().filter(Boolean)
const confirmed = all.filter((f) => f.verdict && f.verdict.refuted === false)
log(all.length + ' raised, ' + confirmed.length + ' survived refutation')
return {
  confirmed,
  refuted: all.filter((f) => f.verdict && f.verdict.refuted).map((f) => f.title),
}
