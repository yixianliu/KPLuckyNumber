"""
HTML文本清洗模块

专门用于去除HTML标签、样式、脚本等冗余内容，
保留纯净的文本内容，便于AI模型理解和分析。

功能：
1. 去除所有HTML标签（div, span, p, h1-h6等）
2. 去除内联样式和脚本内容
3. 转换HTML实体为普通字符
4. 规范化空白字符
5. 保留段落结构和换行
"""

import re
import html
from bs4 import BeautifulSoup
from typing import Optional


class HTMLTextCleaner:
    """
    HTML文本清洗器
    
    将HTML内容转换为纯文本，同时保留段落结构
    """
    
    def __init__(self):
        # HTML实体映射表
        self.html_entities = {
            '&nbsp;': ' ',
            '&amp;': '&',
            '&lt;': '<',
            '&gt;': '>',
            '&quot;': '"',
            '&apos;': "'",
            '&#39;': "'",
            '&mdash;': '—',
            '&ndash;': '–',
            '&hellip;': '…',
            '&ldquo;': '"',
            '&rdquo;': '"',
            '&lsquo;': ''',
            '&rsquo;': ''',
            '&bull;': '•',
            '&middot;': '·',
            '&deg;': '°',
            '&plusmn;': '±',
            '&times;': '×',
            '&divide;': '÷',
            '&nbsp;': ' ',
            '\xa0': ' ',  # 不间断空格
            '\u200b': '',  # 零宽空格
            '\ufeff': '',  # BOM
        }
    
    def clean_html(self, html_content: str) -> str:
        """
        清洗HTML内容，返回纯文本
        
        Args:
            html_content: 原始HTML内容
            
        Returns:
            清洗后的纯文本
        """
        if not html_content:
            return ""
        
        # 如果输入不是HTML（没有标签），直接清洗实体
        if '<' not in html_content:
            return self._clean_text_only(html_content)
        
        try:
            # 使用BeautifulSoup解析
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # 移除script、style、noscript等非内容标签
            for tag in soup(['script', 'style', 'noscript', 'iframe', 'object', 'embed']):
                tag.decompose()
            
            # 移除所有标签的style属性
            for tag in soup.find_all(True):
                tag.attrs = {k: v for k, v in tag.attrs.items() if k in ['href', 'src']}
            
            # 获取纯文本
            text = soup.get_text(separator='\n', strip=True)
            
            # 进一步清洗
            text = self._clean_text_only(text)
            
            return text
            
        except Exception:
            # 解析失败，回退到正则清洗
            return self._clean_html_regex(html_content)
    
    def _clean_text_only(self, text: str) -> str:
        """
        清洗纯文本中的HTML实体和冗余字符
        
        Args:
            text: 纯文本内容
            
        Returns:
            清洗后的文本
        """
        if not text:
            return ""
        
        # 转换HTML实体
        text = html.unescape(text)
        
        # 替换特殊HTML实体
        for entity, char in self.html_entities.items():
            text = text.replace(entity, char)
        
        # 移除残留的HTML标签（不完全的）
        text = re.sub(r'<[^>]+>', '', text)
        
        # 规范化空白字符
        text = re.sub(r'[\t\r\v\f]', ' ', text)  # 替换制表符等为空格
        
        # 移除特殊Unicode字符（零宽空格等）
        text = re.sub(r'[\u200b-\u200f\u2028-\u202f\ufeff]', '', text)
        
        # 规范化连续空格
        text = re.sub(r' +', ' ', text)
        
        # 规范化连续换行
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # 移除行首行尾空格
        lines = [line.strip() for line in text.split('\n')]
        text = '\n'.join(line for line in lines if line)
        
        return text.strip()
    
    def _clean_html_regex(self, html_content: str) -> str:
        """
        使用正则表达式清洗HTML（回退方案）
        
        Args:
            html_content: 原始HTML
            
        Returns:
            清洗后的文本
        """
        if not html_content:
            return ""
        
        # 移除script和style内容
        text = re.sub(r'<script[^>]*>.*?</script>', '', html_content, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r'<noscript[^>]*>.*?</noscript>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # 移除HTML注释
        text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
        
        # 移除所有HTML标签
        text = re.sub(r'<[^>]+>', '', text)
        
        # 转换实体
        text = html.unescape(text)
        
        # 清洗其他内容
        text = self._clean_text_only(text)
        
        return text
    
    def extract_paragraphs(self, text: str) -> list:
        """
        将文本分割为段落
        
        Args:
            text: 清洗后的文本
            
        Returns:
            段落列表
        """
        if not text:
            return []
        
        # 按换行分割
        paragraphs = text.split('\n')
        
        # 过滤空段落
        paragraphs = [p.strip() for p in paragraphs if p.strip()]
        
        return paragraphs
    
    def truncate_text(self, text: str, max_length: int = 5000, suffix: str = '...') -> str:
        """
        截断过长的文本
        
        Args:
            text: 文本内容
            max_length: 最大长度
            suffix: 截断后缀
            
        Returns:
            截断后的文本
        """
        if not text or len(text) <= max_length:
            return text
        
        return text[:max_length - len(suffix)] + suffix


def clean_html_content(html_content: str, max_length: Optional[int] = None) -> str:
    """
    便捷函数：清洗HTML内容为纯文本
    
    Args:
        html_content: 原始HTML内容
        max_length: 最大文本长度（可选）
        
    Returns:
        清洗后的纯文本
    """
    cleaner = HTMLTextCleaner()
    text = cleaner.clean_html(html_content)
    
    if max_length:
        text = cleaner.truncate_text(text, max_length)
    
    return text


def extract_plain_article(article_data: dict) -> dict:
    """
    从文章数据中提取纯文本内容
    
    Args:
        article_data: 包含HTML内容的文章数据
        
    Returns:
        添加了纯文本内容的文章数据
    """
    cleaner = HTMLTextCleaner()
    
    # 清洗标题
    if 'title' in article_data:
        article_data['title_clean'] = cleaner._clean_text_only(article_data['title'])
    
    # 清洗正文内容
    if 'content' in article_data:
        raw_content = article_data['content']
        article_data['content_plain'] = cleaner.clean_html(raw_content)
        article_data['content_length'] = len(article_data['content_plain'])
        article_data['paragraphs'] = cleaner.extract_paragraphs(article_data['content_plain'])
    
    # 清洗作者
    if 'author' in article_data and article_data['author']:
        article_data['author_clean'] = cleaner._clean_text_only(article_data['author'])
    
    # 清洗原始HTML
    if 'raw_html' in article_data:
        del article_data['raw_html']  # 删除原始HTML，节省空间
    
    return article_data


if __name__ == '__main__':
    # 测试示例
    test_html = '''
    <div class="article">
        <h1>2026165期排列五预测分析</h1>
        <p>本期预测如下：</p>
        <style>.red {color: red;}</style>
        <script>console.log("test");</script>
        <div class="content">
            <p>万位关注：<span class="red">0、2、4</span></p>
            <p>千位重点：6、7</p>
            <p>百位推荐：5、8</p>
            <p>十位看好：4、6</p>
            <p>个位主推：5、7</p>
            &nbsp;&nbsp;这是一段包含&nbsp;HTML实体的文本
        </div>
        <div class="author">作者：大肚子</div>
    </div>
    '''
    
    print("=" * 60)
    print("HTML清洗测试")
    print("=" * 60)
    
    cleaner = HTMLTextCleaner()
    clean_text = cleaner.clean_html(test_html)
    
    print("\n【清洗结果】")
    print(clean_text)
    
    print("\n【段落提取】")
    paragraphs = cleaner.extract_paragraphs(clean_text)
    for i, p in enumerate(paragraphs, 1):
        print(f"{i}. {p}")
    
    print("\n【文本长度】")
    print(f"原始HTML: {len(test_html)} 字符")
    print(f"清洗后文本: {len(clean_text)} 字符")