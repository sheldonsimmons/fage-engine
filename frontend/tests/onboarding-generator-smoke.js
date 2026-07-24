const fs = require("fs");
const path = require("path");
const vm = require("vm");

const source = fs.readFileSync(
  path.join(__dirname, "..", "js", "onboarding.js"),
  "utf8"
);

const noopClassList = { add() {}, remove() {}, toggle() {} };
const sandbox = {
  console,
  setTimeout,
  clearTimeout,
  localStorage: {
    getItem() { return null; },
    setItem() {},
  },
  navigator: { clipboard: { writeText: () => Promise.resolve() } },
  window: { location: { href: "" } },
  document: {
    addEventListener() {},
    getElementById() {
      return {
        classList: noopClassList,
        style: {},
        childNodes: [{ textContent: "" }],
        value: "",
        innerHTML: "",
        textContent: "",
        querySelectorAll() { return []; },
        scrollIntoView() {},
      };
    },
    querySelectorAll() { return []; },
  },
};

vm.createContext(sandbox);
vm.runInContext(
  `${source}
globalThis.__onboardingGenerators = {
  salesforce: _genSalesforce,
  servicenow: _genServiceNow,
  hubspot: _genHubSpot,
  dynamics: _genDynamics,
  zendesk: _genZendesk,
  python: _genPython,
  nodejs: _genNode,
  java: _genJava,
  ruby: _genRuby,
  rest: _genRest
};`,
  sandbox
);

const fields = [
  { label: "Description", name: "Description" },
  { label: "Project ID", name: "Project_ID__c" },
];

for (const [name, generator] of Object.entries(sandbox.__onboardingGenerators)) {
  const output = generator("ExampleRecord", "Sales", "Context Agent", fields, []);
  if (typeof output !== "string" || output.length < 100) {
    throw new Error(`${name} generator returned incomplete output`);
  }
  if (output.includes("undefined")) {
    throw new Error(`${name} generator emitted undefined`);
  }
}

console.log("onboarding generator smoke ok");
