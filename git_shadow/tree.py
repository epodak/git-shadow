"""
git_shadow.tree
差异树 (Diff Tree) 渲染引擎：
将影子私密文件与 Git WIP 修改以精美、清晰的树状结构直观呈现。
"""

import os
from typing import Dict, Any, List, Optional
from .utils import Colors, format_size


class TreeNode:
    def __init__(self, name: str, is_dir: bool = False):
        self.name = name
        self.is_dir = is_dir
        self.size: int = 0
        self.kind: str = "shadow"  # "shadow", "modified", "untracked", "deleted"
        self.diff_stat: str = ""   # e.g. "+15 -3"
        self.children: Dict[str, "TreeNode"] = {}

    def add_child(self, name: str, is_dir: bool = False) -> "TreeNode":
        if name not in self.children:
            self.children[name] = TreeNode(name, is_dir=is_dir)
        return self.children[name]


class DiffTreeRenderer:
    def __init__(self, root_name: str, branch: str = ""):
        self.root_name = root_name
        self.branch = branch
        self.root = TreeNode(root_name, is_dir=True)
        self.shadow_count = 0
        self.shadow_total_size = 0
        self.wip_file_count = 0
        self.dir_count = 0

    def add_item(
        self,
        rel_path: str,
        kind: str = "shadow",
        size: int = 0,
        diff_stat: str = ""
    ):
        """将文件相对路径添加到树状结构中"""
        parts = rel_path.replace("\\", "/").strip("/").split("/")
        if not parts:
            return

        curr = self.root
        for idx, part in enumerate(parts[:-1]):
            curr = curr.add_child(part, is_dir=True)

        leaf_name = parts[-1]
        leaf = curr.add_child(leaf_name, is_dir=False)
        leaf.kind = kind
        leaf.size = size
        leaf.diff_stat = diff_stat

        if kind == "shadow":
            self.shadow_count += 1
            self.shadow_total_size += size
        else:
            self.wip_file_count += 1

    def _render_node(
        self,
        node: TreeNode,
        prefix: str = "",
        is_last: bool = True,
        is_root: bool = False
    ) -> List[str]:
        lines = []

        if is_root:
            branch_info = f" {Colors.DIM}({self.branch}){Colors.RESET}" if self.branch else ""
            lines.append(f"{Colors.BOLD}{Colors.CYAN}📦 {node.name}/{Colors.RESET}{branch_info}")
        else:
            connector = "└── " if is_last else "├── "
            if node.is_dir:
                self.dir_count += 1
                line = (
                    f"{Colors.DIM}{prefix}{connector}{Colors.RESET}"
                    f"{Colors.BOLD}{Colors.BLUE}📁 {node.name}/{Colors.RESET}"
                )
            else:
                # 区分类型与高亮
                size_str = f" {Colors.DIM}({format_size(node.size)}){Colors.RESET}" if node.size > 0 else ""
                diff_str = f" {Colors.YELLOW}{node.diff_stat}{Colors.RESET}" if node.diff_stat else ""

                if node.kind == "shadow":
                    tag = f"{Colors.GREEN}[影子]{Colors.RESET}"
                    icon = f"{Colors.GREEN}📄{Colors.RESET}"
                elif node.kind == "modified":
                    tag = f"{Colors.YELLOW}[修改]{Colors.RESET}"
                    icon = f"{Colors.YELLOW}📝{Colors.RESET}"
                elif node.kind == "untracked":
                    tag = f"{Colors.CYAN}[新增]{Colors.RESET}"
                    icon = f"{Colors.CYAN}✨{Colors.RESET}"
                else:
                    tag = f"{Colors.DIM}[其他]{Colors.RESET}"
                    icon = "📄"

                line = (
                    f"{Colors.DIM}{prefix}{connector}{Colors.RESET}"
                    f"{icon} {node.name}{size_str}{diff_str} {tag}"
                )
            lines.append(line)

        # 排序：先目录后文件，按字母序
        children_list = list(node.children.values())
        sorted_children = sorted(children_list, key=lambda x: (not x.is_dir, x.name.lower()))

        new_prefix = prefix + ("    " if is_last else "│   ") if not is_root else ""

        for idx, child in enumerate(sorted_children):
            child_is_last = (idx == len(sorted_children) - 1)
            lines.extend(self._render_node(child, prefix=new_prefix, is_last=child_is_last, is_root=False))

        return lines

    def render(self) -> str:
        """渲染整棵树"""
        self.dir_count = 0
        if not self.root.children:
            return f"{Colors.DIM}(当前工作区基线干净，无待投影的影子文件或修改){Colors.RESET}"

        tree_lines = self._render_node(self.root, is_root=True)
        summary_lines = [
            f"\n{Colors.BOLD}📊 差异投影汇总:{Colors.RESET}",
            f"  • 目录数量: {Colors.CYAN}{self.dir_count}{Colors.RESET} 个",
            f"  • 影子文件: {Colors.GREEN}{self.shadow_count}{Colors.RESET} 个 {Colors.DIM}(共 {format_size(self.shadow_total_size)}){Colors.RESET}",
            f"  • 代码修改: {Colors.YELLOW if self.wip_file_count else Colors.DIM}{self.wip_file_count} 个文件待对齐{Colors.RESET}"
        ]
        return "\n".join(tree_lines) + "\n" + "\n".join(summary_lines)
