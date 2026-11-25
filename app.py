#!/usr/bin/env python3
import aws_cdk as cdk

from infra.keiba_stack import KeibaStack

app = cdk.App()
KeibaStack(app, "KeibaSimulator", env=cdk.Environment(
    account=app.node.try_get_context("account"), region=app.node.try_get_context("region") or "ap-northeast-1"))
app.synth()
