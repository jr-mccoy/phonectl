---
name: Feature request
about: Something droidjig should be able to do
labels: enhancement
---

## The task you are trying to automate

<!-- Describe the phone task, not the API you imagine. What should the agent accomplish? -->

## What is in the way today

<!-- Which command is missing, which one returns the wrong shape, or which capability no
     provider offers. -->

## Does it need a device capability droidjig does not have?

<!-- droidjig runs unrooted over ADB, plus an optional AccessibilityService companion and
     Termux:API. If the feature needs root, a system signature, or a private API, say so —
     it may be out of scope rather than unbuilt. -->

## Safety

<!-- If this would let an agent take a new kind of action: what is the worst outcome if the
     agent gets it wrong, and what risk level should it classify as? Every action goes through
     runtime.run_action, so new verbs need a risk answer before they need code. -->
