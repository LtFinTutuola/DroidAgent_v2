using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Collections.Generic;
using System.Security.Cryptography;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace SemanticMapper
{
    class Program
    {
        const string Sentinel = "===END_OF_CODE===";
        private static readonly CSharpParseOptions ParseOptions =
            new CSharpParseOptions(preprocessorSymbols: new[] { "MonoDroid" });

        // ── Semantic Identity ───────────────────────────────────────────────

        /// <summary>
        /// Returns the fully qualified path: Namespace.Class.Member(Args)
        /// Traverses the parent chain to build a deterministic aggregation key.
        /// </summary>
        private static string GetSemanticIdentity(SyntaxNode node)
        {
            var parts = new List<string>();
            var current = node;

            while (current != null)
            {
                string? part = current switch
                {
                    MethodDeclarationSyntax m => $"{(m.ExplicitInterfaceSpecifier != null ? m.ExplicitInterfaceSpecifier.Name.ToString() + "." : "")}{m.Identifier.Text}{m.TypeParameterList}({string.Join(",", m.ParameterList.Parameters.Select(p => p.Type?.ToString()))})",
                    PropertyDeclarationSyntax p => $"{(p.ExplicitInterfaceSpecifier != null ? p.ExplicitInterfaceSpecifier.Name.ToString() + "." : "")}{p.Identifier.Text}",
                    ConstructorDeclarationSyntax c => $"Constructor({string.Join(",", c.ParameterList.Parameters.Select(p => p.Type?.ToString()))})",
                    FieldDeclarationSyntax f => f.Declaration.Variables.First().Identifier.Text,
                    ClassDeclarationSyntax cls => $"{cls.Identifier.Text}{cls.TypeParameterList}",
                    StructDeclarationSyntax s => $"{s.Identifier.Text}{s.TypeParameterList}",
                    RecordDeclarationSyntax r => $"{r.Identifier.Text}{r.TypeParameterList}",
                    InterfaceDeclarationSyntax i => $"{i.Identifier.Text}{i.TypeParameterList}",
                    NamespaceDeclarationSyntax ns => ns.Name.ToString(),
                    FileScopedNamespaceDeclarationSyntax fns => fns.Name.ToString(),
                    _ => null
                };

                if (part != null)
                    parts.Add(part);

                current = current.Parent;
            }

            parts.Reverse();
            return string.Join(".", parts);
        }

        /// <summary>
        /// Returns the fully qualified identity of the parent class/struct/record.
        /// </summary>
        private static string GetParentIdentity(SyntaxNode node)
        {
            var parent = node.Parent;
            while (parent != null &&
                   !(parent is ClassDeclarationSyntax) &&
                   !(parent is StructDeclarationSyntax) &&
                   !(parent is RecordDeclarationSyntax) &&
                   !(parent is InterfaceDeclarationSyntax))
            {
                parent = parent.Parent;
            }
            return parent != null ? GetSemanticIdentity(parent) : "";
        }

        private static string GetFullSignature(SyntaxNode node)
        {
            return node switch
            {
                MethodDeclarationSyntax m => $"{m.Modifiers} {m.ReturnType} {m.Identifier}{m.ParameterList}".Trim(),
                PropertyDeclarationSyntax p => $"{p.Modifiers} {p.Type} {p.Identifier} {GetAccessors(p)}".Trim(),
                ConstructorDeclarationSyntax c => $"{c.Modifiers} {c.Identifier}{c.ParameterList}".Trim(),
                FieldDeclarationSyntax f => $"{f.Modifiers} {f.Declaration}".Trim(),
                ClassDeclarationSyntax c => $"{c.Modifiers} class {c.Identifier}".Trim(),
                StructDeclarationSyntax s => $"{s.Modifiers} struct {s.Identifier}".Trim(),
                RecordDeclarationSyntax r => $"{r.Modifiers} record {r.Identifier}".Trim(),
                InterfaceDeclarationSyntax i => $"{i.Modifiers} interface {i.Identifier}".Trim(),
                _ => ""
            };
        }

        private static string GetAccessors(PropertyDeclarationSyntax p)
        {
            if (p.AccessorList != null)
            {
                var accessors = p.AccessorList.Accessors.Select(a => $"{a.Modifiers} {a.Keyword};".Trim());
                return "{ " + string.Join(" ", accessors) + " }";
            }
            else if (p.ExpressionBody != null)
            {
                return "{ get; }";
            }
            return "";
        }

        // ── Trivia Stripping ────────────────────────────────────────────────

        /// <summary>
        /// Returns true if the trivia is a comment or preprocessor directive.
        /// </summary>
        private static bool IsCommentOrDirective(SyntaxTrivia t)
        {
            var kind = t.Kind();
            return kind == SyntaxKind.SingleLineCommentTrivia ||
                kind == SyntaxKind.MultiLineCommentTrivia ||
                kind == SyntaxKind.SingleLineDocumentationCommentTrivia ||
                kind == SyntaxKind.MultiLineDocumentationCommentTrivia ||
                kind == SyntaxKind.DocumentationCommentExteriorTrivia ||
                kind == SyntaxKind.XmlComment ||
                kind == SyntaxKind.DisabledTextTrivia ||
                t.IsDirective;
        }

        /// <summary>
        /// Aggressively strip all comment trivia from a syntax node and normalize whitespace.
        /// Returns a flattened, continuous string suitable for character-level comparison.
        /// </summary>
        private static string StripTrivia(SyntaxNode? node)
        {
            if (node == null) return "";

            var cleanNode = node.ReplaceTrivia(
                node.DescendantTrivia(descendIntoTrivia: true)
                    .Concat(node.GetLeadingTrivia())
                    .Concat(node.GetTrailingTrivia())
                    .Where(IsCommentOrDirective),
                (original, _) => SyntaxFactory.ElasticMarker
            );

            return cleanNode.NormalizeWhitespace().ToFullString();
        }

        // ── String Literal Masking (Phase 2.1) ──────────────────────────────

        private class CosmeticChangesRewriter : CSharpSyntaxRewriter
        {
            public override SyntaxNode? VisitVariableDeclaration(VariableDeclarationSyntax node)
            {
                var visited = (VariableDeclarationSyntax?)base.VisitVariableDeclaration(node);
                if (visited == null) return null;
                if (!visited.Type.IsKind(SyntaxKind.IdentifierName) || ((IdentifierNameSyntax)visited.Type).Identifier.Text != "var")
                {
                    return visited.WithType(SyntaxFactory.IdentifierName("var").WithTriviaFrom(visited.Type));
                }
                return visited;
            }

            public override SyntaxNode? VisitForEachStatement(ForEachStatementSyntax node)
            {
                var visited = (ForEachStatementSyntax?)base.VisitForEachStatement(node);
                if (visited == null) return null;
                if (!visited.Type.IsKind(SyntaxKind.IdentifierName) || ((IdentifierNameSyntax)visited.Type).Identifier.Text != "var")
                {
                    return visited.WithType(SyntaxFactory.IdentifierName("var").WithTriviaFrom(visited.Type));
                }
                return visited;
            }

            public override SyntaxNode? VisitImplicitArrayCreationExpression(ImplicitArrayCreationExpressionSyntax node)
            {
                var visited = (ImplicitArrayCreationExpressionSyntax?)base.VisitImplicitArrayCreationExpression(node);
                if (visited == null) return null;
                return SyntaxFactory.CollectionExpression(
                    SyntaxFactory.SeparatedList<CollectionElementSyntax>(
                        visited.Initializer.Expressions.Select(e => (CollectionElementSyntax)SyntaxFactory.ExpressionElement(e))
                    )
                ).WithTriviaFrom(visited);
            }

            public override SyntaxNode? VisitArrayCreationExpression(ArrayCreationExpressionSyntax node)
            {
                var visited = (ArrayCreationExpressionSyntax?)base.VisitArrayCreationExpression(node);
                if (visited == null) return null;
                if (visited.Initializer != null)
                {
                    return SyntaxFactory.CollectionExpression(
                        SyntaxFactory.SeparatedList<CollectionElementSyntax>(
                            visited.Initializer.Expressions.Select(e => (CollectionElementSyntax)SyntaxFactory.ExpressionElement(e))
                        )
                    ).WithTriviaFrom(visited);
                }
                return visited;
            }

            public override SyntaxNode? VisitObjectCreationExpression(ObjectCreationExpressionSyntax node)
            {
                var visited = (ObjectCreationExpressionSyntax?)base.VisitObjectCreationExpression(node);
                if (visited == null) return null;
                return visited.WithType(SyntaxFactory.IdentifierName("var").WithTriviaFrom(visited.Type));
            }

            public override SyntaxNode? VisitImplicitObjectCreationExpression(ImplicitObjectCreationExpressionSyntax node)
            {
                var visited = (ImplicitObjectCreationExpressionSyntax?)base.VisitImplicitObjectCreationExpression(node);
                if (visited == null) return null;
                return SyntaxFactory.ObjectCreationExpression(SyntaxFactory.IdentifierName("var"))
                    .WithArgumentList(visited.ArgumentList)
                    .WithInitializer(visited.Initializer)
                    .WithTriviaFrom(visited);
            }

            public override SyntaxNode? VisitMethodDeclaration(MethodDeclarationSyntax node)
            {
                var visited = (MethodDeclarationSyntax?)base.VisitMethodDeclaration(node);
                if (visited == null) return null;
                
                if (!visited.Modifiers.Any(m => m.IsKind(SyntaxKind.PublicKeyword) || m.IsKind(SyntaxKind.PrivateKeyword) || m.IsKind(SyntaxKind.ProtectedKeyword) || m.IsKind(SyntaxKind.InternalKeyword)))
                {
                    visited = visited.AddModifiers(SyntaxFactory.Token(SyntaxKind.PrivateKeyword));
                }

                if (visited.ExpressionBody != null)
                {
                    var returnStatement = SyntaxFactory.ReturnStatement(visited.ExpressionBody.Expression);
                    var block = SyntaxFactory.Block(returnStatement);
                    return visited.WithExpressionBody(null).WithSemicolonToken(SyntaxFactory.Token(SyntaxKind.None)).WithBody(block);
                }
                return visited;
            }

            public override SyntaxNode? VisitPropertyDeclaration(PropertyDeclarationSyntax node)
            {
                var visited = (PropertyDeclarationSyntax?)base.VisitPropertyDeclaration(node);
                if (visited == null) return null;

                if (!visited.Modifiers.Any(m => m.IsKind(SyntaxKind.PublicKeyword) || m.IsKind(SyntaxKind.PrivateKeyword) || m.IsKind(SyntaxKind.ProtectedKeyword) || m.IsKind(SyntaxKind.InternalKeyword)))
                {
                    visited = visited.AddModifiers(SyntaxFactory.Token(SyntaxKind.PrivateKeyword));
                }

                if (visited.ExpressionBody != null)
                {
                    var getter = SyntaxFactory.AccessorDeclaration(SyntaxKind.GetAccessorDeclaration)
                        .WithBody(SyntaxFactory.Block(SyntaxFactory.ReturnStatement(visited.ExpressionBody.Expression)));
                    return visited.WithExpressionBody(null)
                                  .WithSemicolonToken(SyntaxFactory.Token(SyntaxKind.None))
                                  .WithAccessorList(SyntaxFactory.AccessorList(SyntaxFactory.SingletonList(getter)));
                }
                return visited;
            }

            public override SyntaxNode? VisitClassDeclaration(ClassDeclarationSyntax node)
            {
                var visited = (ClassDeclarationSyntax?)base.VisitClassDeclaration(node);
                if (visited == null) return null;
                if (!visited.Modifiers.Any(m => m.IsKind(SyntaxKind.PublicKeyword) || m.IsKind(SyntaxKind.PrivateKeyword) || m.IsKind(SyntaxKind.ProtectedKeyword) || m.IsKind(SyntaxKind.InternalKeyword)))
                {
                    visited = visited.AddModifiers(SyntaxFactory.Token(SyntaxKind.InternalKeyword));
                }
                return visited;
            }

            public override SyntaxNode? VisitLocalDeclarationStatement(LocalDeclarationStatementSyntax node)
            {
                var visited = (LocalDeclarationStatementSyntax?)base.VisitLocalDeclarationStatement(node);
                if (visited == null) return null;
                if (visited.UsingKeyword.IsKind(SyntaxKind.UsingKeyword))
                {
                    return SyntaxFactory.UsingStatement(
                        declaration: visited.Declaration,
                        expression: null,
                        statement: SyntaxFactory.Block()
                    ).WithTriviaFrom(visited);
                }
                return visited;
            }

            public override SyntaxNode? VisitIsPatternExpression(IsPatternExpressionSyntax node)
            {
                var visited = (IsPatternExpressionSyntax?)base.VisitIsPatternExpression(node);
                if (visited == null) return null;
                if (visited.Pattern is ConstantPatternSyntax cp && cp.Expression.IsKind(SyntaxKind.NullLiteralExpression))
                {
                    return SyntaxFactory.BinaryExpression(SyntaxKind.EqualsExpression, visited.Expression, cp.Expression).WithTriviaFrom(visited);
                }
                if (visited.Pattern is UnaryPatternSyntax up && up.IsKind(SyntaxKind.NotPattern) && up.Pattern is ConstantPatternSyntax cp2 && cp2.Expression.IsKind(SyntaxKind.NullLiteralExpression))
                {
                    return SyntaxFactory.BinaryExpression(SyntaxKind.NotEqualsExpression, visited.Expression, cp2.Expression).WithTriviaFrom(visited);
                }
                return visited;
            }

            public override SyntaxNode? VisitSwitchExpression(SwitchExpressionSyntax node)
            {
                var visited = (SwitchExpressionSyntax?)base.VisitSwitchExpression(node);
                if (visited == null) return null;
                return SyntaxFactory.InvocationExpression(SyntaxFactory.IdentifierName("<CONDITIONAL_BRANCH>")).WithTriviaFrom(visited);
            }

            public override SyntaxNode? VisitSwitchStatement(SwitchStatementSyntax node)
            {
                var visited = (SwitchStatementSyntax?)base.VisitSwitchStatement(node);
                if (visited == null) return null;
                return SyntaxFactory.ExpressionStatement(
                    SyntaxFactory.InvocationExpression(SyntaxFactory.IdentifierName("<CONDITIONAL_BRANCH>"))
                ).WithTriviaFrom(visited);
            }

            public override SyntaxNode? VisitInterpolatedStringExpression(InterpolatedStringExpressionSyntax node)
            {
                return SyntaxFactory.LiteralExpression(SyntaxKind.StringLiteralExpression, SyntaxFactory.Literal("<STR_BUILDER>")).WithTriviaFrom(node);
            }

            public override SyntaxNode? VisitInvocationExpression(InvocationExpressionSyntax node)
            {
                var visited = (InvocationExpressionSyntax?)base.VisitInvocationExpression(node);
                if (visited == null) return null;
                
                if (visited.Expression is MemberAccessExpressionSyntax ma && ma.Name.Identifier.Text == "Format")
                {
                    if (ma.Expression is IdentifierNameSyntax id && id.Identifier.Text == "String")
                        return SyntaxFactory.LiteralExpression(SyntaxKind.StringLiteralExpression, SyntaxFactory.Literal("<STR_BUILDER>")).WithTriviaFrom(visited);
                    if (ma.Expression is PredefinedTypeSyntax pt && pt.Keyword.IsKind(SyntaxKind.StringKeyword))
                        return SyntaxFactory.LiteralExpression(SyntaxKind.StringLiteralExpression, SyntaxFactory.Literal("<STR_BUILDER>")).WithTriviaFrom(visited);
                }
                return visited;
            }

            public override SyntaxNode? VisitBinaryExpression(BinaryExpressionSyntax node)
            {
                var visited = (BinaryExpressionSyntax?)base.VisitBinaryExpression(node);
                if (visited == null) return null;

                if (node.IsKind(SyntaxKind.AddExpression) || node.IsKind(SyntaxKind.AddAssignmentExpression))
                {
                    if (node.Left.IsKind(SyntaxKind.StringLiteralExpression) || node.Right.IsKind(SyntaxKind.StringLiteralExpression) ||
                        node.Left.IsKind(SyntaxKind.InterpolatedStringExpression) || node.Right.IsKind(SyntaxKind.InterpolatedStringExpression))
                    {
                        return SyntaxFactory.LiteralExpression(SyntaxKind.StringLiteralExpression, SyntaxFactory.Literal("<STR_BUILDER>")).WithTriviaFrom(node);
                    }
                }
                return visited;
            }

            public override SyntaxNode? VisitArgument(ArgumentSyntax node)
            {
                var visited = (ArgumentSyntax?)base.VisitArgument(node);
                if (visited == null) return null;
                
                if (visited.NameColon != null)
                {
                    return visited.WithNameColon(null).WithTriviaFrom(visited);
                }
                return visited;
            }

            public override SyntaxNode? VisitAssignmentExpression(AssignmentExpressionSyntax node)
            {
                var visited = (AssignmentExpressionSyntax?)base.VisitAssignmentExpression(node);
                if (visited == null) return null;

                if (node.IsKind(SyntaxKind.CoalesceAssignmentExpression))
                {
                    return SyntaxFactory.AssignmentExpression(
                        SyntaxKind.SimpleAssignmentExpression,
                        visited.Left,
                        SyntaxFactory.BinaryExpression(SyntaxKind.CoalesceExpression, visited.Left, visited.Right)
                    ).WithTriviaFrom(visited);
                }

                return visited;
            }
        }

        /// <summary>
        /// Clone an AST and replace all string literal expressions with a neutral
        /// token ("<STR>") to sterilize aesthetic/hardcoded diffs. We measure only
        /// control flow and architectural churn, not string content changes.
        /// Also masks interpolated strings and character literals.
        /// </summary>
        private static SyntaxNode MaskStringLiterals(SyntaxNode node)
        {
            // Replace StringLiteralExpression nodes
            var masked = node.ReplaceNodes(
                node.DescendantNodes().OfType<LiteralExpressionSyntax>()
                    .Where(n => n.Kind() == SyntaxKind.StringLiteralExpression ||
                                n.Kind() == SyntaxKind.CharacterLiteralExpression),
                (original, _) => SyntaxFactory.LiteralExpression(
                    SyntaxKind.StringLiteralExpression,
                    SyntaxFactory.Literal("<STR>"))
            );

            // Replace InterpolatedStringExpression nodes with a plain string literal
            masked = masked.ReplaceNodes(
                masked.DescendantNodes().OfType<InterpolatedStringExpressionSyntax>(),
                (original, _) => SyntaxFactory.LiteralExpression(
                    SyntaxKind.StringLiteralExpression,
                    SyntaxFactory.Literal("<STR>"))
            );

            return masked;
        }

        private static SyntaxNode MaskAst(SyntaxNode node)
        {
            var stringMasked = MaskStringLiterals(node);
            var rewriter = new CosmeticChangesRewriter();
            return rewriter.Visit(stringMasked) ?? stringMasked;
        }

        // ── AST Hash Short-Circuit (Phase 2.2) ─────────────────────────────

        /// <summary>
        /// Compute a deterministic SHA256 hash of a masked, trivia-stripped AST node.
        /// Used for fast equality checks: if hash(old) == hash(new), diff_score = 0.0.
        /// This bypasses the expensive TSED calculation for unchanged methods.
        /// </summary>
        private static string ComputeAstHash(SyntaxNode? node)
        {
            if (node == null) return "";

            var masked = MaskAst(node);
            var stripped = masked.NormalizeWhitespace().ToFullString();

            // Remove all remaining whitespace for position-independent hashing
            stripped = System.Text.RegularExpressions.Regex.Replace(stripped, @"\s+", "");

            using var sha256 = SHA256.Create();
            var hashBytes = sha256.ComputeHash(Encoding.UTF8.GetBytes(stripped));
            return Convert.ToHexString(hashBytes);
        }

        // ── GumTree Structural Diffing (Phase 2.3) ─────────────────────────

        /// <summary>
        /// Edit operation types produced by the GumTree matcher.
        /// </summary>
        private enum EditType { Match, Insert, Delete, Update, Move }

        /// <summary>
        /// A single edit operation in the GumTree edit script.
        /// </summary>
        private struct EditOp
        {
            public EditType Type;
            public SyntaxNode? OldNode;
            public SyntaxNode? NewNode;
        }

        public class NormalizedEditOp
        {
            public string operation { get; set; }
            public string node_type { get; set; }
        }

        /// <summary>
        /// Lightweight representation of a syntax node for hashing and comparison.
        /// </summary>
        private struct GumTreeNode
        {
            public SyntaxNode Node;
            public string Label;       // SyntaxKind name
            public int Height;         // Height in the AST
            public string SubtreeHash; // SHA-256 of the normalized subtree
        }

        /// <summary>
        /// Compute the height of a syntax node (longest path to a leaf).
        /// </summary>
        private static int ComputeHeight(SyntaxNode node)
        {
            var children = node.ChildNodes().ToList();
            if (children.Count == 0) return 1;
            return 1 + children.Max(c => ComputeHeight(c));
        }

        /// <summary>
        /// Compute a deterministic hash for a subtree for identity comparison.
        /// Uses SyntaxKind labels only (structure-based, not content-based).
        /// </summary>
        private static string ComputeSubtreeStructureHash(SyntaxNode node)
        {
            var sb = new StringBuilder();
            BuildStructureString(node, sb);
            using var sha = SHA256.Create();
            var bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(sb.ToString()));
            return Convert.ToHexString(bytes);
        }

        private static void BuildStructureString(SyntaxNode node, StringBuilder sb)
        {
            sb.Append(node.Kind().ToString());
            sb.Append('(');
            foreach (var child in node.ChildNodes())
            {
                BuildStructureString(child, sb);
                sb.Append(',');
            }
            sb.Append(')');
        }

        /// <summary>
        /// Build a lookup of GumTreeNode info for all nodes in a tree.
        /// </summary>
        private static Dictionary<SyntaxNode, GumTreeNode> BuildNodeMap(SyntaxNode root)
        {
            var map = new Dictionary<SyntaxNode, GumTreeNode>();
            foreach (var node in root.DescendantNodesAndSelf())
            {
                map[node] = new GumTreeNode
                {
                    Node = node,
                    Label = node.Kind().ToString(),
                    Height = ComputeHeight(node),
                    SubtreeHash = ComputeSubtreeStructureHash(node)
                };
            }
            return map;
        }

        /// <summary>
        /// GumTree Phase 1: Top-Down Greedy Subtree Matching.
        /// Matches entire subtrees that are structurally identical (same hash)
        /// starting from the largest subtrees down to minHeight.
        /// </summary>
        private static Dictionary<SyntaxNode, SyntaxNode> TopDownMatch(
            SyntaxNode oldRoot, SyntaxNode newRoot,
            Dictionary<SyntaxNode, GumTreeNode> oldMap,
            Dictionary<SyntaxNode, GumTreeNode> newMap,
            int minHeight = 2)
        {
            var matched = new Dictionary<SyntaxNode, SyntaxNode>();
            var matchedNew = new HashSet<SyntaxNode>();

            // Build hash → nodes index for the new tree
            var newHashIndex = new Dictionary<string, List<SyntaxNode>>();
            foreach (var kvp in newMap)
            {
                if (!newHashIndex.ContainsKey(kvp.Value.SubtreeHash))
                    newHashIndex[kvp.Value.SubtreeHash] = new List<SyntaxNode>();
                newHashIndex[kvp.Value.SubtreeHash].Add(kvp.Key);
            }

            // Process old nodes in decreasing height order (largest subtrees first)
            var oldNodesByHeight = oldMap.Values
                .Where(n => n.Height >= minHeight)
                .OrderByDescending(n => n.Height)
                .ToList();

            foreach (var oldInfo in oldNodesByHeight)
            {
                // Skip if already matched as part of a larger subtree
                if (matched.ContainsKey(oldInfo.Node)) continue;

                if (newHashIndex.TryGetValue(oldInfo.SubtreeHash, out var candidates))
                {
                    // Find the best unmatched candidate (prefer same label, then closest position)
                    SyntaxNode? bestMatch = null;
                    foreach (var candidate in candidates)
                    {
                        if (matchedNew.Contains(candidate)) continue;
                        if (newMap[candidate].Label != oldInfo.Label) continue;
                        bestMatch = candidate;
                        break;
                    }

                    if (bestMatch != null)
                    {
                        // Match entire subtrees
                        MatchSubtrees(oldInfo.Node, bestMatch, matched, matchedNew, oldMap, newMap);
                    }
                }
            }

            return matched;
        }

        /// <summary>
        /// Recursively match all nodes in two structurally identical subtrees.
        /// </summary>
        private static void MatchSubtrees(
            SyntaxNode oldNode, SyntaxNode newNode,
            Dictionary<SyntaxNode, SyntaxNode> matched,
            HashSet<SyntaxNode> matchedNew,
            Dictionary<SyntaxNode, GumTreeNode> oldMap,
            Dictionary<SyntaxNode, GumTreeNode> newMap)
        {
            if (matched.ContainsKey(oldNode) || matchedNew.Contains(newNode)) return;

            matched[oldNode] = newNode;
            matchedNew.Add(newNode);

            var oldChildren = oldNode.ChildNodes().ToList();
            var newChildren = newNode.ChildNodes().ToList();

            int minCount = Math.Min(oldChildren.Count, newChildren.Count);
            for (int i = 0; i < minCount; i++)
            {
                MatchSubtrees(oldChildren[i], newChildren[i], matched, matchedNew, oldMap, newMap);
            }
        }

        /// <summary>
        /// GumTree Phase 2: Bottom-Up Recovery Matching.
        /// For each unmatched old node, find the best unmatched new node using
        /// a Dice coefficient on the already-matched descendants.
        /// </summary>
        private static void BottomUpMatch(
            SyntaxNode oldRoot, SyntaxNode newRoot,
            Dictionary<SyntaxNode, SyntaxNode> matched,
            Dictionary<SyntaxNode, GumTreeNode> oldMap,
            Dictionary<SyntaxNode, GumTreeNode> newMap,
            double minDice = 0.25)
        {
            var matchedNewSet = new HashSet<SyntaxNode>(matched.Values);

            // Process unmatched old nodes in bottom-up order (leaves first)
            var unmatchedOld = oldRoot.DescendantNodesAndSelf()
                .Where(n => !matched.ContainsKey(n))
                .OrderBy(n => oldMap.ContainsKey(n) ? oldMap[n].Height : 0)
                .ToList();

            foreach (var oldNode in unmatchedOld)
            {
                if (matched.ContainsKey(oldNode)) continue;
                if (!oldMap.ContainsKey(oldNode)) continue;

                var oldLabel = oldMap[oldNode].Label;

                // Find candidate new nodes with the same label
                var candidates = newRoot.DescendantNodesAndSelf()
                    .Where(n => !matchedNewSet.Contains(n) &&
                                newMap.ContainsKey(n) &&
                                newMap[n].Label == oldLabel)
                    .ToList();

                SyntaxNode? bestCandidate = null;
                double bestDice = minDice;

                foreach (var candidate in candidates)
                {
                    double dice = ComputeDiceCoefficient(oldNode, candidate, matched);
                    if (dice > bestDice)
                    {
                        bestDice = dice;
                        bestCandidate = candidate;
                    }
                }

                if (bestCandidate != null)
                {
                    matched[oldNode] = bestCandidate;
                    matchedNewSet.Add(bestCandidate);
                }
            }
        }

        /// <summary>
        /// Compute the Dice coefficient between two nodes based on their
        /// already-matched descendants.
        /// Dice = 2 * |matched_descendants| / (|old_descendants| + |new_descendants|)
        /// </summary>
        private static double ComputeDiceCoefficient(
            SyntaxNode oldNode, SyntaxNode newNode,
            Dictionary<SyntaxNode, SyntaxNode> matched)
        {
            var oldDescendants = oldNode.DescendantNodesAndSelf().ToHashSet();
            var newDescendants = newNode.DescendantNodesAndSelf().ToHashSet();

            int matchedCount = 0;
            foreach (var od in oldDescendants)
            {
                if (matched.TryGetValue(od, out var mn) && newDescendants.Contains(mn))
                    matchedCount++;
            }

            int total = oldDescendants.Count + newDescendants.Count;
            if (total == 0) return 0.0;

            return (2.0 * matchedCount) / total;
        }

        /// <summary>
        /// Detect move operations: a node is considered "moved" if it is matched
        /// but its parent in the old tree is matched to a different node than
        /// the parent of its match in the new tree.
        /// </summary>
        private static bool IsMove(
            SyntaxNode oldNode, SyntaxNode newNode,
            Dictionary<SyntaxNode, SyntaxNode> matched)
        {
            var oldParent = oldNode.Parent;
            var newParent = newNode.Parent;

            if (oldParent == null || newParent == null) return false;

            // If old parent is matched to something other than new parent → move
            if (matched.TryGetValue(oldParent, out var matchedParent))
            {
                return matchedParent != newParent;
            }

            // If old parent is unmatched but new parent is matched → move
            return true;
        }

        /// <summary>
        /// Generate the complete edit script from the GumTree matching.
        /// Classifies every node into: Match, Insert, Delete, Update, or Move.
        /// </summary>
        private static List<EditOp> GenerateEditScript(
            SyntaxNode oldRoot, SyntaxNode newRoot,
            Dictionary<SyntaxNode, SyntaxNode> matched,
            Dictionary<SyntaxNode, GumTreeNode> oldMap,
            Dictionary<SyntaxNode, GumTreeNode> newMap)
        {
            var ops = new List<EditOp>();
            var matchedNewSet = new HashSet<SyntaxNode>(matched.Values);

            // Process matched pairs: Match, Update, or Move
            foreach (var kvp in matched)
            {
                var oldNode = kvp.Key;
                var newNode = kvp.Value;

                bool labelChanged = oldMap.ContainsKey(oldNode) && newMap.ContainsKey(newNode) &&
                                    oldMap[oldNode].Label != newMap[newNode].Label;

                if (labelChanged)
                {
                    ops.Add(new EditOp { Type = EditType.Update, OldNode = oldNode, NewNode = newNode });
                }
                else if (IsMove(oldNode, newNode, matched))
                {
                    ops.Add(new EditOp { Type = EditType.Move, OldNode = oldNode, NewNode = newNode });
                }
                else
                {
                    ops.Add(new EditOp { Type = EditType.Match, OldNode = oldNode, NewNode = newNode });
                }
            }

            // Deleted nodes: in old tree but not matched
            foreach (var oldNode in oldRoot.DescendantNodesAndSelf())
            {
                if (!matched.ContainsKey(oldNode))
                {
                    ops.Add(new EditOp { Type = EditType.Delete, OldNode = oldNode });
                }
            }

            // Inserted nodes: in new tree but not matched to anything
            foreach (var newNode in newRoot.DescendantNodesAndSelf())
            {
                if (!matchedNewSet.Contains(newNode))
                {
                    ops.Add(new EditOp { Type = EditType.Insert, NewNode = newNode });
                }
            }

            return ops;
        }

        /// <summary>
        /// Calculate the GumTree-based structural distance between two AST nodes.
        /// Returns a normalized score ∈ [0, 1]:
        ///   0.0 = identical trees, 1.0 = entirely different trees.
        /// 
        /// Formula: D = (|Insert| + |Delete| + |Update| + 0.5 × |Move|) / (|T1| + |T2|)
        /// Move operations weighted at 0.5 because moves are structural reorganization,
        /// not destructive changes.
        /// </summary>
        private static double CalculateStructuralDistance(SyntaxNode maskedOld, SyntaxNode maskedNew, out List<NormalizedEditOp> normalizedEditScript)
        {
            var oldMap = BuildNodeMap(maskedOld);
            var newMap = BuildNodeMap(maskedNew);

            // Phase 1: Top-Down Greedy Matching
            var matched = TopDownMatch(maskedOld, maskedNew, oldMap, newMap);

            // Phase 2: Bottom-Up Recovery
            BottomUpMatch(maskedOld, maskedNew, matched, oldMap, newMap);

            // Generate edit script
            var editScript = GenerateEditScript(maskedOld, maskedNew, matched, oldMap, newMap);

            normalizedEditScript = editScript.Select(e => new NormalizedEditOp {
                operation = e.Type.ToString(),
                node_type = (e.OldNode ?? e.NewNode)?.Kind().ToString() ?? "Unknown"
            }).ToList();

            // Count operations
            int inserts = editScript.Count(e => e.Type == EditType.Insert);
            int deletes = editScript.Count(e => e.Type == EditType.Delete);
            int updates = editScript.Count(e => e.Type == EditType.Update);
            int moves = editScript.Count(e => e.Type == EditType.Move);

            int totalNodes = oldMap.Count + newMap.Count;
            if (totalNodes == 0) return 0.0;

            double distance = (inserts + deletes + updates + 0.5 * moves) / totalNodes;
            return Math.Min(1.0, Math.Max(0.0, distance));
        }

        /// <summary>
        /// Calculate the GumTree-based structural diff_score between two AST nodes.
        /// Applies string masking, hash short-circuit, and GumTree structural diffing.
        /// Returns a normalized score ∈ [0, 1]:
        ///   0.0 = identical trees, 1.0 = entirely different trees.
        /// 
        /// Short-circuit rules:
        ///   - Both null → 0.0
        ///   - One null (pure creation/deletion) → 1.0
        ///   - Same AST hash (handles method reordering within file) → 0.0
        /// 
        /// Also outputs the old/new AST hashes for downstream use.
        /// </summary>
        private static double CalculateDiffScore(SyntaxNode? oldNode, SyntaxNode? newNode,
            out string hashOld, out string hashNew, out List<NormalizedEditOp> editScript)
        {
            hashOld = "";
            hashNew = "";
            editScript = new List<NormalizedEditOp>();

            // Both null: no change
            if (oldNode == null && newNode == null) return 0.0;

            // Pure creation or pure deletion
            if (oldNode == null || newNode == null)
            {
                hashOld = ComputeAstHash(oldNode);
                hashNew = ComputeAstHash(newNode);
                return 1.0;
            }

            // Mask string literals and cosmetic changes before comparison
            var maskedOld = MaskAst(oldNode);
            var maskedNew = MaskAst(newNode);

            // Hash short-circuit: identical masked ASTs → 0.0
            hashOld = ComputeAstHash(oldNode);
            hashNew = ComputeAstHash(newNode);
            if (hashOld == hashNew) return 0.0;

            // Full GumTree structural diffing
            return CalculateStructuralDistance(maskedOld, maskedNew, out editScript);
        }

        /// <summary>
        /// Backward-compatible overload without hash output parameters.
        /// </summary>
        private static double CalculateDiffScore(SyntaxNode? oldNode, SyntaxNode? newNode)
        {
            return CalculateDiffScore(oldNode, newNode, out _, out _, out _);
        }

        // ── Cognitive Complexity ─────────────────────────────────────────────

        private class CognitiveComplexityWalker : CSharpSyntaxWalker
        {
            public int Score { get; private set; } = 0;

            public override void VisitVariableDeclaration(VariableDeclarationSyntax node) { Score += 1; base.VisitVariableDeclaration(node); }
            public override void VisitAssignmentExpression(AssignmentExpressionSyntax node) { Score += 1; base.VisitAssignmentExpression(node); }
            public override void VisitReturnStatement(ReturnStatementSyntax node) { Score += 1; base.VisitReturnStatement(node); }
            
            public override void VisitInvocationExpression(InvocationExpressionSyntax node) { Score += 2; base.VisitInvocationExpression(node); }
            public override void VisitObjectCreationExpression(ObjectCreationExpressionSyntax node) { Score += 2; base.VisitObjectCreationExpression(node); }

            public override void VisitIfStatement(IfStatementSyntax node) { Score += 3; base.VisitIfStatement(node); }
            public override void VisitForStatement(ForStatementSyntax node) { Score += 3; base.VisitForStatement(node); }
            public override void VisitWhileStatement(WhileStatementSyntax node) { Score += 3; base.VisitWhileStatement(node); }
            public override void VisitSwitchSection(SwitchSectionSyntax node) { Score += 3; base.VisitSwitchSection(node); }
            public override void VisitCatchClause(CatchClauseSyntax node) { Score += 3; base.VisitCatchClause(node); }
        }

        private static int CalculateCognitiveComplexity(SyntaxNode? node)
        {
            if (node == null) return 0;
            if (node is FieldDeclarationSyntax) return 0; // Short-circuit for fields
            var walker = new CognitiveComplexityWalker();
            walker.Visit(node);
            return walker.Score;
        }

        private static string GetObjectType(SyntaxNode node)
        {
            return node switch
            {
                FieldDeclarationSyntax _ => "field",
                PropertyDeclarationSyntax _ => "property",
                ConstructorDeclarationSyntax _ => "constructor",
                MethodDeclarationSyntax _ => "method",
                _ => "method"
            };
        }

        // ── Semantic Token Extraction ────────────────────────────────────────

        private static readonly HashSet<SyntaxKind> LogicalTokenKinds = new HashSet<SyntaxKind>
        {
            // Operators
            SyntaxKind.PlusToken, SyntaxKind.MinusToken, SyntaxKind.AsteriskToken, SyntaxKind.SlashToken, SyntaxKind.PercentToken,
            SyntaxKind.EqualsEqualsToken, SyntaxKind.ExclamationEqualsToken, SyntaxKind.LessThanToken, SyntaxKind.LessThanEqualsToken,
            SyntaxKind.GreaterThanToken, SyntaxKind.GreaterThanEqualsToken, SyntaxKind.AmpersandAmpersandToken, SyntaxKind.BarBarToken,
            SyntaxKind.AmpersandToken, SyntaxKind.BarToken, SyntaxKind.CaretToken, SyntaxKind.TildeToken, SyntaxKind.ExclamationToken,
            SyntaxKind.EqualsToken, SyntaxKind.PlusEqualsToken, SyntaxKind.MinusEqualsToken, SyntaxKind.AsteriskEqualsToken,
            SyntaxKind.SlashEqualsToken, SyntaxKind.PercentEqualsToken, SyntaxKind.AmpersandEqualsToken, SyntaxKind.BarEqualsToken,
            SyntaxKind.CaretEqualsToken, SyntaxKind.LessThanLessThanToken, SyntaxKind.GreaterThanGreaterThanToken,
            SyntaxKind.LessThanLessThanEqualsToken, SyntaxKind.GreaterThanGreaterThanEqualsToken, SyntaxKind.QuestionQuestionToken,
            SyntaxKind.QuestionQuestionEqualsToken, SyntaxKind.QuestionToken, SyntaxKind.ColonToken, SyntaxKind.DotToken, SyntaxKind.CommaToken,

            // Control Flow
            SyntaxKind.IfKeyword, SyntaxKind.ElseKeyword, SyntaxKind.SwitchKeyword, SyntaxKind.CaseKeyword, SyntaxKind.DefaultKeyword,
            SyntaxKind.ForKeyword, SyntaxKind.ForEachKeyword, SyntaxKind.WhileKeyword, SyntaxKind.DoKeyword, SyntaxKind.BreakKeyword,
            SyntaxKind.ContinueKeyword, SyntaxKind.GotoKeyword, SyntaxKind.ReturnKeyword, SyntaxKind.YieldKeyword, SyntaxKind.ThrowKeyword,
            SyntaxKind.TryKeyword, SyntaxKind.CatchKeyword, SyntaxKind.FinallyKeyword, SyntaxKind.LockKeyword, SyntaxKind.UsingKeyword,
            SyntaxKind.AwaitKeyword,

            // Literals
            SyntaxKind.NumericLiteralToken, SyntaxKind.StringLiteralToken, SyntaxKind.TrueKeyword, SyntaxKind.FalseKeyword,
            SyntaxKind.NullKeyword, SyntaxKind.CharacterLiteralToken
        };

        private static List<string> GetLogicalTokens(SyntaxNode? node)
        {
            if (node == null) return new List<string>();

            return node.DescendantTokens()
                       .Where(t => LogicalTokenKinds.Contains(t.Kind()))
                       .Select(t => t.ValueText)
                       .ToList();
        }

        private static bool IsLogicalChange(SyntaxNode? oldNode, SyntaxNode? newNode)
        {
            var oldTokens = GetLogicalTokens(oldNode);
            var newTokens = GetLogicalTokens(newNode);

            if (oldTokens.Count != newTokens.Count) return true;

            for (int i = 0; i < oldTokens.Count; i++)
            {
                if (oldTokens[i] != newTokens[i]) return true;
            }

            return false;
        }

        // ── Semantic Node Extraction ────────────────────────────────────────

        /// <summary>
        /// Given code and 1-based line numbers, find the semantic nodes
        /// (method, property, constructor) that contain those lines.
        /// </summary>
        private static List<SyntaxNode> GetSemanticNodes(string code, List<int> lines)
        {
            if (string.IsNullOrWhiteSpace(code)) return new List<SyntaxNode>();

            var tree = CSharpSyntaxTree.ParseText(code, ParseOptions);
            var root = tree.GetRoot();
            var semanticNodes = new HashSet<SyntaxNode>();

            foreach (var lineNum in lines)
            {
                var text = tree.GetText();
                if (lineNum <= 0 || lineNum > text.Lines.Count) continue;

                var line = text.Lines[lineNum - 1];
                var node = root.FindNode(line.Span);

                // Walk up to the nearest semantic boundary
                while (node != null &&
                       !(node is MethodDeclarationSyntax) &&
                       !(node is PropertyDeclarationSyntax) &&
                       !(node is ConstructorDeclarationSyntax) &&
                       !(node is FieldDeclarationSyntax) &&
                       !(node is ClassDeclarationSyntax) &&
                       !(node is StructDeclarationSyntax) &&
                       !(node is RecordDeclarationSyntax) &&
                       !(node is InterfaceDeclarationSyntax))
                {
                    node = node.Parent;
                }

                if (node != null)
                {
                    if (node is TypeDeclarationSyntax typeNode && 
                        (node is ClassDeclarationSyntax || node is StructDeclarationSyntax || 
                         node is RecordDeclarationSyntax || node is InterfaceDeclarationSyntax))
                    {
                        // If we landed on a class/struct/record/interface, pick the member intersecting the line
                        foreach (var member in typeNode.Members)
                        {
                            if ((member is MethodDeclarationSyntax ||
                                 member is PropertyDeclarationSyntax ||
                                 member is ConstructorDeclarationSyntax ||
                                 member is FieldDeclarationSyntax) &&
                                member.Span.IntersectsWith(line.Span))
                            {
                                semanticNodes.Add(member);
                            }
                        }
                    }
                    else
                    {
                        semanticNodes.Add(node);
                    }
                }
            }

            return semanticNodes.OrderBy(n => n.SpanStart).ToList();
        }

        /// <summary>
        /// Simplified identity for matching across old/new trees.
        /// Matches by method name + parameter types, ensuring method reordering
        /// within a file does NOT produce false diffs.
        /// </summary>
        private static string GetLocalIdentity(SyntaxNode node)
        {
            return node switch
            {
                MethodDeclarationSyntax m =>
                    $"method:{(m.ExplicitInterfaceSpecifier != null ? m.ExplicitInterfaceSpecifier.Name.ToString() + "." : "")}{m.Identifier.Text}{m.TypeParameterList}({string.Join(",", m.ParameterList.Parameters.Select(p => p.Type?.ToString()))})",
                PropertyDeclarationSyntax p => $"prop:{(p.ExplicitInterfaceSpecifier != null ? p.ExplicitInterfaceSpecifier.Name.ToString() + "." : "")}{p.Identifier.Text}",
                ConstructorDeclarationSyntax c =>
                    $"ctor:({string.Join(",", c.ParameterList.Parameters.Select(p => p.Type?.ToString()))})",
                FieldDeclarationSyntax f => $"field:{f.Declaration.Variables.First().Identifier.Text}",
                _ => node.ToString().Substring(0, Math.Min(50, node.ToString().Length))
            };
        }

        // ── Signature Change Detection ──────────────────────────────────────

        /// <summary>
        /// Find the immediate parent type declaration (class/struct/record/interface) of a node.
        /// </summary>
        private static SyntaxNode? FindParentTypeDeclaration(SyntaxNode node)
        {
            var parent = node.Parent;
            while (parent != null &&
                   !(parent is ClassDeclarationSyntax) &&
                   !(parent is StructDeclarationSyntax) &&
                   !(parent is RecordDeclarationSyntax) &&
                   !(parent is InterfaceDeclarationSyntax))
            {
                parent = parent.Parent;
            }
            return parent;
        }

        /// <summary>
        /// Find a matching type declaration in a tree by semantic identity.
        /// </summary>
        private static SyntaxNode? FindMatchingTypeInTree(SyntaxNode root, string typeIdentity)
        {
            if (string.IsNullOrEmpty(typeIdentity)) return null;
            return root.DescendantNodes()
                .Where(n => n is ClassDeclarationSyntax ||
                            n is StructDeclarationSyntax ||
                            n is RecordDeclarationSyntax ||
                            n is InterfaceDeclarationSyntax)
                .FirstOrDefault(n => GetSemanticIdentity(n) == typeIdentity);
        }

        /// <summary>
        /// Count the number of members of the same kind within a type declaration.
        /// For constructors: counts all constructors.
        /// For methods: counts methods with the same name.
        /// For properties/fields: counts by identifier name.
        /// </summary>
        private static int CountMembersOfSameKind(SyntaxNode typeDeclaration, SyntaxNode targetMember)
        {
            var allChildren = typeDeclaration.ChildNodes();

            return targetMember switch
            {
                ConstructorDeclarationSyntax =>
                    allChildren.Count(m => m is ConstructorDeclarationSyntax),
                MethodDeclarationSyntax method =>
                    allChildren.Count(m =>
                        m is MethodDeclarationSyntax md && md.Identifier.Text == method.Identifier.Text),
                PropertyDeclarationSyntax prop =>
                    allChildren.Count(m =>
                        m is PropertyDeclarationSyntax pd && pd.Identifier.Text == prop.Identifier.Text),
                FieldDeclarationSyntax field =>
                    allChildren.Count(m =>
                        m is FieldDeclarationSyntax fd &&
                        fd.Declaration.Variables.Any(v =>
                            field.Declaration.Variables.Any(fv => v.Identifier.Text == fv.Identifier.Text))),
                _ => 0
            };
        }

        /// <summary>
        /// Check if an unmatched member is a signature change rather than a true add/delete.
        /// Compares member counts in the source parent class vs the corresponding parent in the other tree.
        /// Same count implies signature change; different count implies genuine add/remove.
        /// </summary>
        private static bool IsSignatureChange(SyntaxNode member, SyntaxNode otherTree)
        {
            var parentType = FindParentTypeDeclaration(member);
            if (parentType == null) return false;

            var parentIdentity = GetSemanticIdentity(parentType);
            var otherParent = FindMatchingTypeInTree(otherTree, parentIdentity);
            if (otherParent == null) return false;

            int countInSource = CountMembersOfSameKind(parentType, member);
            int countInOther = CountMembersOfSameKind(otherParent, member);

            return countInSource == countInOther;
        }

        // ── CENSUS_EXTRACT Command ──────────────────────────────────────────

        /// <summary>
        /// Process a CENSUS_EXTRACT command: match old/new semantic nodes,
        /// return fully-qualified signatures, sanitized code pairs, and GumTree diff_score.
        /// 
        /// Method reordering robustness: Methods are matched by GetLocalIdentity
        /// (name + parameter types), not by position. If a method is merely moved
        /// within the file, it matches correctly and the hash short-circuit
        /// produces diff_score = 0.0.
        /// </summary>
        private static string ProcessCensusExtract(
            string oldCode, string newCode,
            List<int> oldLns, List<int> newLns)
        {
            var oldNodes = GetSemanticNodes(oldCode, oldLns);
            var newNodes = GetSemanticNodes(newCode, newLns);

            var oldTree = string.IsNullOrWhiteSpace(oldCode)
                ? null : CSharpSyntaxTree.ParseText(oldCode, ParseOptions).GetRoot();
            var newTree = string.IsNullOrWhiteSpace(newCode)
                ? null : CSharpSyntaxTree.ParseText(newCode, ParseOptions).GetRoot();

            var results = new List<object>();
            var processedNewIdentities = new HashSet<string>();

            // Process old nodes first, matching to new nodes by local identity
            foreach (var oldNode in oldNodes)
            {
                var localId = GetLocalIdentity(oldNode);
                SyntaxNode? matchedNew = null;

                if (newTree != null)
                {
                    matchedNew = newTree.DescendantNodes()
                        .FirstOrDefault(n =>
                            (n is MethodDeclarationSyntax ||
                             n is PropertyDeclarationSyntax ||
                             n is ConstructorDeclarationSyntax ||
                             n is FieldDeclarationSyntax) &&
                            GetLocalIdentity(n) == localId);
                }

                if (matchedNew != null)
                    processedNewIdentities.Add(localId);

                // Calculate GumTree diff_score with masking and short-circuit
                double diffScore;
                bool isNewOrDead = (oldNode == null || matchedNew == null);

                if (!isNewOrDead && oldNode is FieldDeclarationSyntax oldF && matchedNew is FieldDeclarationSyntax newF)
                {
                    bool typeChanged = oldF.Declaration.Type.ToString() != newF.Declaration.Type.ToString();
                    bool modifiersChanged = oldF.Modifiers.ToString() != newF.Modifiers.ToString();
                    
                    diffScore = (typeChanged || modifiersChanged) ? 1.0 : 0.0;
                }
                else
                {
                    diffScore = CalculateDiffScore(oldNode, matchedNew);
                }

                // Signature change detection: if old node has no match in new tree,
                // check if parent class has the same member count → signature change
                if (isNewOrDead && matchedNew == null && newTree != null)
                {
                    if (IsSignatureChange(oldNode, newTree))
                        continue; // Skip: new-pass will handle the renamed version
                }

                int rawScore = isNewOrDead
                    ? CalculateCognitiveComplexity(oldNode ?? matchedNew)
                    : Math.Abs(CalculateCognitiveComplexity(matchedNew) - CalculateCognitiveComplexity(oldNode));

                results.Add(new
                {
                    full_signature = GetFullSignature(oldNode),
                    signature = GetSemanticIdentity(oldNode),
                    parent_signature = GetParentIdentity(oldNode),
                    sanitized_old_code = StripTrivia(oldNode),
                    sanitized_new_code = StripTrivia(matchedNew),
                    diff_score = diffScore,
                    structural_score = diffScore,
                    raw_complexity_score = rawScore,
                    object_type = GetObjectType(oldNode ?? matchedNew!)
                });
            }

            // Process new-only nodes (additions)
            foreach (var newNode in newNodes)
            {
                var localId = GetLocalIdentity(newNode);
                if (processedNewIdentities.Contains(localId)) continue;

                SyntaxNode? matchedOld = null;
                if (oldTree != null)
                {
                    matchedOld = oldTree.DescendantNodes()
                        .FirstOrDefault(n =>
                            (n is MethodDeclarationSyntax ||
                             n is PropertyDeclarationSyntax ||
                             n is ConstructorDeclarationSyntax ||
                             n is FieldDeclarationSyntax) &&
                            GetLocalIdentity(n) == localId);
                }

                // Calculate GumTree diff_score with masking and short-circuit
                double diffScore;
                bool isNewOrDead = (matchedOld == null || newNode == null);

                if (!isNewOrDead && matchedOld is FieldDeclarationSyntax oldF && newNode is FieldDeclarationSyntax newF)
                {
                    bool typeChanged = oldF.Declaration.Type.ToString() != newF.Declaration.Type.ToString();
                    bool modifiersChanged = oldF.Modifiers.ToString() != newF.Modifiers.ToString();
                    
                    diffScore = (typeChanged || modifiersChanged) ? 1.0 : 0.0;
                }
                else
                {
                    diffScore = CalculateDiffScore(matchedOld, newNode);
                }

                // Signature change detection: if new node has no match in old tree,
                // check if parent class has the same member count → signature change
                if (isNewOrDead && matchedOld == null && oldTree != null)
                {
                    if (IsSignatureChange(newNode, oldTree))
                    {
                        isNewOrDead = false;
                        diffScore = 0.0; // Replaced by signature_changed_diff_score in Python
                    }
                }

                int rawScore = isNewOrDead
                    ? CalculateCognitiveComplexity(matchedOld ?? newNode)
                    : Math.Abs(CalculateCognitiveComplexity(newNode) - CalculateCognitiveComplexity(matchedOld));

                results.Add(new
                {
                    full_signature = GetFullSignature(newNode),
                    signature = GetSemanticIdentity(newNode),
                    parent_signature = GetParentIdentity(newNode),
                    sanitized_old_code = StripTrivia(matchedOld),
                    sanitized_new_code = StripTrivia(newNode),
                    diff_score = diffScore,
                    structural_score = diffScore,
                    is_new_or_dead = isNewOrDead,
                    raw_complexity_score = rawScore,
                    object_type = GetObjectType(matchedOld ?? newNode!)
                });
            }

            return System.Text.Json.JsonSerializer.Serialize(results);
        }

        // ── BASELINE_EXTRACT Command ────────────────────────────────────────

        /// <summary>
        /// Process a BASELINE_EXTRACT command: extract all semantic nodes from the code.
        /// </summary>
        private static string ProcessBaselineExtract(string code)
        {
            if (string.IsNullOrWhiteSpace(code)) return "[]";

            var tree = CSharpSyntaxTree.ParseText(code, ParseOptions);
            var root = tree.GetRoot();

            var members = root.DescendantNodes().Where(n => 
                n is MethodDeclarationSyntax ||
                n is PropertyDeclarationSyntax ||
                n is ConstructorDeclarationSyntax ||
                n is FieldDeclarationSyntax).ToList();

            var results = new List<object>();

            foreach (var node in members)
            {
                results.Add(new
                {
                    signature = GetSemanticIdentity(node),
                    parent_signature = GetParentIdentity(node),
                    raw_complexity_score = CalculateCognitiveComplexity(node),
                    object_type = GetObjectType(node)
                });
            }

            return System.Text.Json.JsonSerializer.Serialize(results);
        }

        // ── Main Entry Point ────────────────────────────────────────────────

        static void Main(string[] args)
        {
            Console.InputEncoding = Encoding.UTF8;
            Console.OutputEncoding = Encoding.UTF8;

            Console.WriteLine("READY");

            while (true)
            {
                string? commandLine = Console.ReadLine();
                if (commandLine == null || commandLine == "EXIT") break;

                try
                {
                    if (commandLine.StartsWith("CENSUS_EXTRACT|||"))
                    {
                        var parts = commandLine.Split(new[] { "|||" }, StringSplitOptions.None);
                        var oldLns = parts.Length > 1 && !string.IsNullOrWhiteSpace(parts[1])
                            ? parts[1].Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries)
                                .Select(int.Parse).ToList()
                            : new List<int>();
                        var newLns = parts.Length > 2 && !string.IsNullOrWhiteSpace(parts[2])
                            ? parts[2].Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries)
                                .Select(int.Parse).ToList()
                            : new List<int>();

                        var codeBuilder = new StringBuilder();
                        while (true)
                        {
                            var line = Console.ReadLine();
                            if (line == null || line == Sentinel) break;
                            codeBuilder.AppendLine(line);
                        }

                        var combined = codeBuilder.ToString();
                        var codeParts = combined.Split(
                            new[] { "---DELIMITER---" }, StringSplitOptions.None);
                        var oldCode = codeParts[0].Trim();
                        var newCode = codeParts.Length > 1 ? codeParts[1].Trim() : "";

                        var result = ProcessCensusExtract(oldCode, newCode, oldLns, newLns);
                        Console.WriteLine(result);
                    }
                    else if (commandLine.StartsWith("BASELINE_EXTRACT|||"))
                    {
                        var codeBuilder = new StringBuilder();
                        while (true)
                        {
                            var line = Console.ReadLine();
                            if (line == null || line == Sentinel) break;
                            codeBuilder.AppendLine(line);
                        }

                        var result = ProcessBaselineExtract(codeBuilder.ToString());
                        Console.WriteLine(result);
                    }
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine($"Error: {ex.Message}\n{ex.StackTrace}");
                }
                finally
                {
                    Console.WriteLine(Sentinel);
                }
            }
        }
    }
}
