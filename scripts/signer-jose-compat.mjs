// Receives only a synthetic token and public JWK on stdin.
import { compactVerify, createLocalJWKSet } from "jose";
let input = "";
for await (const part of process.stdin) input += part;
const { public: key, token } = JSON.parse(input);
await compactVerify(token, createLocalJWKSet({keys: [key]}), {algorithms: ["RS256"]});
console.log("RSA-4096 accepted");
