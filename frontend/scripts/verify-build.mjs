import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repository = path.resolve(frontend, '..');
const output = path.join(repository, 'site', 'react-dist');

for (const [template, folder] of [
  ['site/index.html', 'site'],
  ['site/sre-studio.html', 'site'],
  ['intelligence/group_a/site/group-a.html', 'intelligence/group_a/site'],
]) {
  const html = fs.readFileSync(path.join(repository, template), 'utf8');
  const scripts = [...html.matchAll(/<script\b[^>]*\bsrc="\/([^"]+\.js)"/g)]
    .map((match) => match[1]);
  assert(scripts.length > 0, 'Missing scripts in ' + template);
  for (const name of scripts) {
    assert(fs.existsSync(path.join(frontend, 'src', 'legacy', name.replace(/\.js$/, '.ts'))),
      'Missing TypeScript module: ' + name + ' from ' + folder);
    assert(fs.existsSync(path.join(output, 'legacy', name)),
      'Missing compiled module: ' + name);
  }
}
const entry = fs.readFileSync(path.join(output, 'index.html'), 'utf8');
assert.match(entry, /type="module"/);
assert.match(entry, /assets\/index-[^"]+\.js/);
console.log('React build and TypeScript legacy coverage: OK');
