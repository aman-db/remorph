package com.databricks.labs.remorph.antlrlinter

class OrphanedRule extends ANTLRv4ParserBaseListener {

  private val ruleTracker = new RuleTracker

  /**
   * Records that a rule has been defined in the parser
   */
  override def enterParserRuleSpec(ctx: ANTLRv4Parser.ParserRuleSpecContext): Unit = {

    val ruleSymbol = ctx.RULE_REF().getSymbol
    val ruleDefinition = new RuleDefinition(ruleSymbol.getLine, ruleSymbol.getText)
    ruleTracker.addRuleDef(ruleDefinition)
  }


  override def enterRuleref(ctx: ANTLRv4Parser.RulerefContext): Unit = {
    val ruleReference = new RuleReference(ctx.start.getLine, ctx.start.getCharPositionInLine, ctx.stop.getCharPositionInLine, ctx.getText)
    ruleTracker.addRuleRef(ruleReference)
  }
}
