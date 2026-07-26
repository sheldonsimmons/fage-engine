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
};
globalThis.__universalSetup = {
  renderConnectionPlan: renderObConnectionPlan,
  verificationHtml: _universalVerificationHtml,
  setStage: setUniversalSetupStage,
  runTest: runUniversalSetupTest,
  activate: activateUniversalConnection,
  renderDiscovery: renderObDiscoveryCard,
  restoreDiscovery: restoreOAuthDiscovery,
  approveDiscovery: approveDiscoveredMapping
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

for (const name of ["salesforce", "servicenow", "hubspot"]) {
  const output = sandbox.__onboardingGenerators[name](
    "ExampleRecord",
    "Sales",
    "Context Agent",
    fields,
    []
  );
  for (const marker of [
    "contract_version",
    "2026-07-26",
    "source",
    "actor",
    "work",
    "request",
    "sync_if_missing",
  ]) {
    if (!output.includes(marker)) {
      throw new Error(`${name} generator is missing universal contract marker: ${marker}`);
    }
  }
}

for (const name of [
  "renderConnectionPlan",
  "verificationHtml",
  "setStage",
  "runTest",
  "activate",
  "renderDiscovery",
  "restoreDiscovery",
  "approveDiscovery",
]) {
  if (typeof sandbox.__universalSetup[name] !== "function") {
    throw new Error(`universal setup function is missing: ${name}`);
  }
}

for (const marker of [
  "/api/integrations/contract",
  "/api/integrations/connections",
  "/objects",
  "/discover",
  "/mapping",
  "/api/route",
  "obPlatformInstalled",
]) {
  if (!source.includes(marker)) {
    throw new Error(`universal setup verification is missing: ${marker}`);
  }
}

console.log("onboarding generator smoke ok");
